(() => {
    const STATUS_URL = "/api/system/remote-flow-control";
    const NAVIGATION_URL = `${STATUS_URL}/navigation`;
    let timer = null;
    let stopped = false;

    function showBanner(status) {
        let banner = document.getElementById("remote-flow-control-banner");
        if (!status?.enabled) {
            banner?.remove();
            document.body.classList.remove("remote-flow-control-enabled");
            return;
        }
        if (!banner) {
            banner = document.createElement("div");
            banner.id = "remote-flow-control-banner";
            banner.className = "remote-flow-control-banner";
            banner.setAttribute("role", "status");
            document.body.prepend(banner);
        }
        banner.textContent = `Signed remote Flow testing enabled · ${status.remaining_runs} run${status.remaining_runs === 1 ? "" : "s"} remaining this hour · Emergency off: System > Updates`;
        document.body.classList.add("remote-flow-control-enabled");
    }

    async function json(response) {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(payload.detail || `HTTP ${response.status}`);
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    async function consumeNavigation() {
        const payload = await fetch(NAVIGATION_URL, { cache: "no-store" }).then(json);
        const intent = payload.intent;
        const runId = Number(intent?.run_id);
        if (!intent || !Number.isSafeInteger(runId) || runId <= 0) return;
        const commandId = String(intent.command_id || "");
        const response = await fetch(`${NAVIGATION_URL}/${encodeURIComponent(commandId)}/ack`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
        });
        await json(response);
        localStorage.setItem("metronome-remote-navigation", commandId);
        const destination = `/flow-runs/${runId}`;
        if (location.pathname !== destination) location.assign(destination);
    }

    async function poll() {
        if (stopped) return;
        try {
            const status = await fetch(STATUS_URL, { cache: "no-store" }).then(json);
            window._remoteFlowControlStatus = status;
            showBanner(status);
            if (status.enabled) await consumeNavigation();
        } catch (error) {
            if (error.status === 403) {
                stopped = true;
                showBanner(null);
                return;
            }
        } finally {
            if (!stopped) timer = window.setTimeout(poll, 2000);
        }
    }

    window.refreshRemoteFlowControl = () => {
        stopped = false;
        if (timer) window.clearTimeout(timer);
        return poll();
    };
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", poll, { once: true });
    } else {
        poll();
    }
})();
