import { useEffect, useState } from "react";
import { api } from "./api/client";
import type { AnomalyGroup, Incident, Target } from "./api/types";
import { AnomalyStream } from "./components/AnomalyStream";
import { ContainedPanel } from "./components/ContainedPanel";
import { Footer } from "./components/Footer";
import { Greeting, Masthead } from "./components/Masthead";
import { MeasuresPanel } from "./components/MeasuresPanel";
import { ProtocolsPanel } from "./components/ProtocolsPanel";
import { RecordsPanel } from "./components/RecordsPanel";
import { SilencedPanel } from "./components/SilencedPanel";
import { Tiles } from "./components/Tiles";
import { UnassignedPanel } from "./components/UnassignedPanel";
import { usePolling } from "./hooks/usePolling";
import { ignorePayload } from "./lib/anomalies";
import { PAGE_SIZE, PENDING_REFRESH_MS, REFRESH_MS } from "./lib/constants";

export default function App() {
  const [windowMinutes, setWindowMinutes] = useState(60);
  const [containerFilter, setContainerFilter] = useState<string[]>([]);
  const [findingsPage, setFindingsPage] = useState(0);
  const [measuresPage, setMeasuresPage] = useState(0);
  const [lastSurvey, setLastSurvey] = useState<Date | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const overview = usePolling(() => api.overview(), REFRESH_MS, []);
  const anomalies = usePolling(
    () => api.anomalies(windowMinutes, containerFilter),
    (data) => (data?.groups_pending ? PENDING_REFRESH_MS : REFRESH_MS),
    [windowMinutes, containerFilter],
  );
  const containers = usePolling(
    () => api.containers(windowMinutes),
    REFRESH_MS,
    [windowMinutes],
  );
  const findings = usePolling(
    () => api.findings(PAGE_SIZE, findingsPage * PAGE_SIZE),
    REFRESH_MS,
    [findingsPage],
  );
  const measures = usePolling(
    () => api.remediations(PAGE_SIZE, measuresPage * PAGE_SIZE),
    REFRESH_MS,
    [measuresPage],
  );
  const ignored = usePolling(() => api.ignored(), REFRESH_MS, []);
  const targets = usePolling(() => api.targets(), REFRESH_MS, []);

  // Stamp the footer whenever a survey lands.
  useEffect(() => {
    if (anomalies.data) setLastSurvey(new Date());
  }, [anomalies.data]);

  // A cleanup can shrink an archive beneath the page being viewed; step back.
  useEffect(() => {
    const total = findings.data?.total ?? 0;
    const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
    if (findingsPage > lastPage) setFindingsPage(lastPage);
  }, [findings.data, findingsPage]);
  useEffect(() => {
    const total = measures.data?.total ?? 0;
    const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
    if (measuresPage > lastPage) setMeasuresPage(lastPage);
  }, [measures.data, measuresPage]);

  const incidents = anomalies.data?.incidents ?? [];
  const unassigned = incidents.filter((i) => i.bucket === "unassigned");
  const contained = incidents.filter((i) => i.bucket !== "unassigned");
  const byFp = new Map(unassigned.map((i) => [i.fingerprint, i]));

  const uplinkError = anomalies.error?.message ?? actionError ?? undefined;
  const ok = !uplinkError;

  // Runs a mutation, records any failure on the uplink, and refreshes the
  // affected views on success.
  async function guard(action: () => Promise<unknown>) {
    try {
      await action();
      setActionError(null);
    } catch (exc) {
      setActionError((exc as Error).message);
    }
  }

  const refetchAnomalyViews = () =>
    Promise.all([anomalies.refetch(), ignored.refetch()]);

  const onSilence = (incident: Incident) =>
    guard(async () => {
      await api.ignore(ignorePayload(incident.fingerprint, incident));
      await refetchAnomalyViews();
    });

  const onSilenceGroup = (fingerprints: string[]) => {
    const list = fingerprints.filter(Boolean);
    if (!list.length) return;
    void guard(async () => {
      await api.ignoreBatch(list.map((fp) => ignorePayload(fp, byFp.get(fp))));
      await refetchAnomalyViews();
    });
  };

  const onSilenceAll = () => {
    if (!unassigned.length) return;
    if (
      !window.confirm(
        `Silence all ${unassigned.length} unassigned anomalies? I shall designate every one as noise, Reclaimer, and suppress it from the stream.`,
      )
    ) {
      return;
    }
    void guard(async () => {
      await api.ignoreBatch(
        unassigned.map((inc) => ignorePayload(inc.fingerprint, inc)),
      );
      await refetchAnomalyViews();
    });
  };

  const onCreateRule = (
    group: AnomalyGroup,
    service: string,
    pattern: string,
    note: string,
  ) =>
    guard(async () => {
      await api.createRule(service, pattern, group.title || "", note);
      await refetchAnomalyViews();
    });

  const onRestore = (fingerprint: string) =>
    guard(async () => {
      await api.restore(fingerprint);
      await refetchAnomalyViews();
    });

  const onSaveNote = (fingerprint: string, note: string) =>
    guard(() => api.updateNote(fingerprint, note));

  const onLiftRule = (id: number) =>
    guard(async () => {
      await api.deleteRule(id);
      await refetchAnomalyViews();
    });

  const onSaveRule = (id: number, title: string, note: string) =>
    guard(async () => {
      await api.updateRuleMetadata(id, title, note);
      await ignored.refetch();
    });

  // Target save propagates errors so the protocol form can show them inline.
  const onSaveTarget = async (payload: Target) => {
    await api.saveTarget(payload);
    await Promise.all([
      targets.refetch(),
      overview.refetch(),
      anomalies.refetch(),
    ]);
  };

  const onDeleteTarget = (id: string) =>
    guard(async () => {
      await api.deleteTarget(id);
      await Promise.all([
        targets.refetch(),
        overview.refetch(),
        anomalies.refetch(),
      ]);
    });

  const dash = (v: number | undefined): number | string => v ?? "—";

  return (
    <>
      <Masthead ok={ok} />
      <Greeting ok={ok} detail={uplinkError} />
      <main>
        <Tiles
          anomalies={dash(anomalies.data?.error_events)}
          unassigned={anomalies.data ? unassigned.length : "—"}
          findings={dash(overview.data?.counts.findings)}
          measures={dash(overview.data?.counts.remediations)}
        />
        <AnomalyStream
          data={anomalies.data}
          windowMinutes={windowMinutes}
          onWindowChange={setWindowMinutes}
          availableContainers={containers.data?.containers ?? []}
          selectedContainers={containerFilter}
          onContainersChange={setContainerFilter}
        />
        <UnassignedPanel
          unassigned={unassigned}
          groups={anomalies.data?.groups}
          groupsPending={anomalies.data?.groups_pending}
          error={anomalies.error?.message}
          onSilence={onSilence}
          onSilenceAll={onSilenceAll}
          onSilenceGroup={onSilenceGroup}
          onCreateRule={onCreateRule}
        />
        <SilencedPanel
          data={ignored.data}
          onRestore={onRestore}
          onSaveNote={onSaveNote}
          onLiftRule={onLiftRule}
          onSaveRule={onSaveRule}
        />
        <ContainedPanel contained={contained} />
        <ProtocolsPanel
          targets={targets.data?.targets ?? []}
          onSave={onSaveTarget}
          onDelete={onDeleteTarget}
        />
        <div className="columns">
          <RecordsPanel data={findings.data} page={findingsPage} onPage={setFindingsPage} />
          <MeasuresPanel data={measures.data} page={measuresPage} onPage={setMeasuresPage} />
        </div>
      </main>
      <Footer
        targets={overview.data?.targets ?? []}
        lastSurvey={lastSurvey}
      />
    </>
  );
}
