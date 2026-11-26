
"use strict";

(() => {

    const $ = id => document.getElementById(id);

    const csrf = $("tv-csrf").value;

    let busy = false;
    let polling = false;

    function message(text) {
        $("tv-message").textContent = text;
    }

    function render(data) {

        const settings = data.settings;
        const state = data.state;
        const queue = data.queue;

        const enabled = settings.enabled === 1;

        $("tv-status").textContent =
            enabled ? "LIVE TV ENABLED" : "LIVE TV STOPPED";

        $("tv-status").classList.toggle(
            "online", enabled
        );

        document.querySelectorAll("[data-mode]")
            .forEach(button => {

                button.classList.toggle(
                    "selected",
                    button.dataset.mode === state.display_mode
                );

            });

        document.querySelectorAll("[data-toggle]")
            .forEach(input => {

                input.checked =
                    settings[input.dataset.toggle] === 1;

            });

        for (const [key, value] of Object.entries(queue)) {

            const element = $(`count-${key}`);

            if (element) element.textContent = value;
        }
    }

    async function refresh() {

        if (busy || polling) return;

        polling = true;

        try {

            const response = await fetch(
                "/api/admin/tv/status",
                {
                    credentials: "same-origin",
                    cache: "no-store"
                }
            );

            if (!response.ok) {
                throw new Error("Unable to load TV status");
            }

            const data = await response.json();

            if (!busy) render(data);

        } catch (error) {

            message(error.message);

            $("tv-status").textContent =
                "STATUS UNAVAILABLE";

            $("tv-status").classList.remove("online");

        } finally {

            polling = false;
        }
    }

    async function control(action) {

        if (busy) return;

        busy = true;

        const controls = document.querySelectorAll(
            "button, input[type=checkbox]"
        );

        controls.forEach(element => {
            element.disabled = true;
        });

        try {

            const response = await fetch(
                "/api/admin/tv/control",
                {
                    method: "POST",
                    credentials: "same-origin",
                    cache: "no-store",
                    headers: {
                        "Content-Type": "application/json",
                        "X-TAM-TV-CSRF": csrf
                    },
                    body: JSON.stringify(action)
                }
            );

            const data = await response.json();

            if (!response.ok) {

                throw new Error(
                    typeof data.detail === "string"
                        ? data.detail
                        : "Unable to update Live TV"
                );
            }

            render(data);

            message("Live TV settings updated.");

        } catch (error) {

            message(error.message);

        } finally {

            busy = false;

            controls.forEach(element => {
                element.disabled = false;
            });

            refresh();
        }
    }

    document.querySelectorAll("[data-setting]")
        .forEach(button => {

            button.addEventListener("click", () => {

                control({
                    action: "setting",
                    setting: button.dataset.setting,
                    value: Number(button.dataset.value)
                });

            });

        });

    document.querySelectorAll("[data-mode]")
        .forEach(button => {

            button.addEventListener("click", () => {

                control({
                    action: "mode",
                    mode: button.dataset.mode
                });

            });

        });

    document.querySelectorAll("[data-toggle]")
        .forEach(input => {

            input.addEventListener("change", () => {

                control({
                    action: "setting",
                    setting: input.dataset.toggle,
                    value: input.checked ? 1 : 0
                });

            });

        });

    refresh();

    setInterval(refresh, 3000);

})();
