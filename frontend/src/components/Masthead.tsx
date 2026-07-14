export function Masthead({ ok }: { ok: boolean }) {
  return (
    <header className="masthead">
      <div className="eye" aria-hidden="true">
        <div className="eye-iris" />
      </div>
      <div className="masthead-id">
        <h1>343 Guilty Spark</h1>
        <p className="masthead-sub">Monitor · Installation Log Surveillance</p>
      </div>
      <div className="uplink" id="uplink">
        <span className={`uplink-dot ${ok ? "ok" : "bad"}`} />
        <span>{ok ? "Uplink nominal" : "Uplink degraded"}</span>
      </div>
    </header>
  );
}

export function Greeting({ ok, detail }: { ok: boolean; detail?: string }) {
  const text = ok
    ? "Greetings, Reclaimer. I have surveyed the installation's log streams. The relevant anomalies are catalogued below."
    : `A regrettable malfunction, Reclaimer — the archive uplink is not responding. ${detail || ""}`;
  return (
    <p className="greeting" id="greeting">
      {text}
    </p>
  );
}
