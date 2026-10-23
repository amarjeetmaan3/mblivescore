/* Controller-side GitHub Actions manager.
 * The fine-grained token is held in memory only and is never persisted.
 * Required repository permission: Actions (read and write).
 */
(() => {
  const OWNER = "amarjeetmaan3";
  const REPO = "mblivescore";
  const API = "https://api.github.com";
  let token = "";
  let workflows = [];
  let runs = [];

  const definitions = [
    {
      file: "sync-cricbuzz.yml",
      name: "Cricbuzz Match Data Sync",
      description: "Real fetch + Firebase current score/archive write. Also runs on a 5-minute schedule.",
      caution: true
    },
    {
      file: "test.yml",
      name: "Cricbuzz Fetch Validation",
      description: "Read-only validation/dry run. Does not write the fetched score/archive to Firebase."
    },
    {
      file: "debug.yml",
      name: "Debug Explore",
      description: "Investigates Cricbuzz page HTML/JSON structures. Currently contains old match ID 151554."
    }
  ];

  const $ = id => document.getElementById(id);
  const status = (message, bad = false) => {
    const el = $("workflowManagerStatus");
    if (el) {
      el.textContent = message;
      el.style.color = bad ? "#fca5a5" : "#86efac";
    }
  };

  async function api(path, options = {}) {
    if (!token) throw new Error("Connect a GitHub fine-grained token first.");
    const response = await fetch(API + path, {
      ...options,
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {})
      }
    });
    if (!response.ok) {
      let detail = "";
      try {
        const body = await response.json();
        detail = body.message || "";
      } catch (_) {}
      throw new Error("GitHub API " + response.status + (detail ? ": " + detail : ""));
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function latestRunFor(file) {
    return runs.find(run => (run.path || "").endsWith("/" + file)) || null;
  }

  function runLabel(run) {
    if (!run) return "No run found yet";
    const when = run.created_at ? new Date(run.created_at).toLocaleString() : "";
    return (run.status === "completed" ? (run.conclusion || "completed") : run.status) + (when ? " · " + when : "");
  }

  function render() {
    const host = $("workflowManagerList");
    if (!host) return;
    host.innerHTML = "";
    definitions.forEach(def => {
      const found = workflows.find(w => w.path === ".github/workflows/" + def.file);
      const last = latestRunFor(def.file);
      const card = document.createElement("div");
      card.style.cssText = "border:1px solid var(--line);border-radius:6px;padding:9px;margin:7px 0;background:var(--navy-deep)";
      const title = document.createElement("div");
      title.style.cssText = "font-size:14px;font-weight:700;color:var(--gold-2);margin-bottom:3px";
      title.textContent = def.name;
      const desc = document.createElement("div");
      desc.style.cssText = "font-size:11px;color:var(--muted);margin-bottom:6px";
      desc.textContent = def.description;
      const latest = document.createElement("div");
      latest.style.cssText = "font-size:11px;margin-bottom:7px";
      latest.textContent = found ? "Latest: " + runLabel(last) : "Workflow file not found on main";
      const actions = document.createElement("div");
      actions.style.cssText = "display:flex;gap:6px";
      const runBtn = document.createElement("button");
      runBtn.className = "btn-primary";
      runBtn.textContent = "RUN";
      runBtn.disabled = !found || !token;
      runBtn.style.flex = "1";
      runBtn.onclick = async () => {
        if (def.caution && !confirm("This runs the real sync and writes fetched data to Firebase. Continue?")) return;
        runBtn.disabled = true;
        try {
          await api("/repos/" + OWNER + "/" + REPO + "/actions/workflows/" + def.file + "/dispatches", {
            method: "POST", body: JSON.stringify({ ref: "main" })
          });
          status(def.name + " dispatch request accepted. Refresh in a few seconds to see the run.");
          setTimeout(() => refresh(), 4000);
        } catch (e) {
          status(e.message, true);
        } finally {
          runBtn.disabled = !token;
        }
      };
      actions.appendChild(runBtn);
      if (last && last.status !== "completed") {
        const cancelBtn = document.createElement("button");
        cancelBtn.className = "btn-danger";
        cancelBtn.textContent = "CANCEL RUN";
        cancelBtn.style.flex = "1";
        cancelBtn.onclick = async () => {
          if (!confirm("Cancel this active run of " + def.name + "?")) return;
          cancelBtn.disabled = true;
          try {
            await api("/repos/" + OWNER + "/" + REPO + "/actions/runs/" + last.id + "/cancel", { method: "POST" });
            status("Cancel requested for " + def.name + ".");
            await refresh();
          } catch (e) {
            status(e.message, true);
            cancelBtn.disabled = false;
          }
        };
        actions.appendChild(cancelBtn);
      }
      card.append(title, desc, latest, actions);
      host.appendChild(card);
    });

    const notFound = $("workflowManagerNotFound");
    if (notFound) {
      const names = ["Quick Button Match Skin", "Page Build Deployment"];
      notFound.textContent = names.join(" and ") + " are not present as workflow files in amarjeetmaan3/mblivescore's main branch. They may belong to another repository; no run buttons are shown for them until their actual workflow files are identified.";
    }
  }

  async function refresh() {
    if (!token) {
      render();
      status("Enter a GitHub fine-grained token with Actions read/write permission to enable Run and Cancel.", true);
      return;
    }
    status("Checking repository workflows…");
    try {
      const [workflowData, runData] = await Promise.all([
        api("/repos/" + OWNER + "/" + REPO + "/actions/workflows?per_page=100"),
        api("/repos/" + OWNER + "/" + REPO + "/actions/runs?per_page=50")
      ]);
      workflows = workflowData.workflows || [];
      runs = (runData.workflow_runs || []).sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      render();
      status("Connected. " + workflows.length + " workflow(s) found; latest runs refreshed.");
    } catch (e) {
      workflows = [];
      runs = [];
      render();
      status(e.message + " Check token access and Actions permissions.", true);
    }
  }

  window.openWorkflowManager = function() {
    $("workflowManagerModal").classList.add("active");
    render();
    if (token) refresh();
    else status("Enter a GitHub fine-grained token with Actions read/write permission to enable Run and Cancel.", true);
  };
  window.closeWorkflowManager = function() {
    $("workflowManagerModal").classList.remove("active");
  };
  window.connectWorkflowManager = async function() {
    const entered = $("workflowManagerToken").value.trim();
    if (!entered) {
      status("Paste the token first.", true);
      return;
    }
    token = entered;
    $("workflowManagerToken").value = "";
    await refresh();
  };
  window.refreshWorkflowManager = refresh;
})();
