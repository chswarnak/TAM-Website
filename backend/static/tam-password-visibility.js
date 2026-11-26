
// TAM-PASSWORD-VISIBILITY-V2
// Closed eye = hidden password.
// Open eye = visible password.
// Icon stays inside the right edge of the input.

(function () {
    "use strict";

    const eyeOpen = `
        <svg xmlns="http://www.w3.org/2000/svg"
             width="22" height="22"
             viewBox="0 0 24 24"
             fill="none" stroke="currentColor"
             stroke-width="2"
             stroke-linecap="round"
             stroke-linejoin="round"
             aria-hidden="true">
            <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/>
            <circle cx="12" cy="12" r="3"/>
        </svg>
    `;

    const eyeClosed = `
        <svg xmlns="http://www.w3.org/2000/svg"
             width="22" height="22"
             viewBox="0 0 24 24"
             fill="none" stroke="currentColor"
             stroke-width="2"
             stroke-linecap="round"
             stroke-linejoin="round"
             aria-hidden="true">
            <path d="M3 3l18 18"/>
            <path d="M10.6 5.1A10.7 10.7 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-4 4.8"/>
            <path d="M6.2 6.2C3.6 8 2 12 2 12s3.5 7 10 7c1.8 0 3.4-.5 4.8-1.3"/>
            <path d="M10 10a3 3 0 0 0 4 4"/>
        </svg>
    `;

    function installStyles() {
        if (document.getElementById("tam-password-eye-styles")) {
            return;
        }

        const style = document.createElement("style");
        style.id = "tam-password-eye-styles";

        style.textContent = `
            .tam-password-wrapper {
                position: relative !important;
                display: block !important;
                width: 100% !important;
                box-sizing: border-box !important;
            }

            .tam-password-wrapper > input {
                width: 100% !important;
                box-sizing: border-box !important;
                padding-right: 52px !important;
            }

            .tam-password-wrapper > button.tam-password-toggle {
                position: absolute !important;
                top: 50% !important;
                right: 6px !important;
                left: auto !important;
                bottom: auto !important;
                transform: translateY(-50%) !important;

                width: 42px !important;
                height: 42px !important;
                min-width: 42px !important;
                max-width: 42px !important;

                margin: 0 !important;
                padding: 0 !important;

                display: flex !important;
                align-items: center !important;
                justify-content: center !important;

                background: transparent !important;
                background-color: transparent !important;
                border: none !important;
                box-shadow: none !important;

                color: #465575 !important;
                cursor: pointer !important;
                z-index: 10 !important;
                border-radius: 6px !important;
            }

            .tam-password-wrapper > button.tam-password-toggle:focus-visible {
                outline: 2px solid #76328a !important;
                outline-offset: -3px !important;
            }

            .tam-password-toggle svg {
                width: 22px !important;
                height: 22px !important;
                display: block !important;
                flex-shrink: 0 !important;
            }
        `;

        document.head.appendChild(style);
    }

    function initializePasswordVisibility() {
        installStyles();

        document.querySelectorAll(
            'input[type="password"]'
        ).forEach((input) => {

            if (input.dataset.tamVisibilityReady === "2") {
                return;
            }

            // Remove only the previous TAM visibility wrapper
            // if one already exists.
            let wrapper = input.parentElement;

            if (
                !wrapper ||
                !wrapper.classList.contains("tam-password-wrapper")
            ) {
                wrapper = document.createElement("div");
                wrapper.className = "tam-password-wrapper";

                input.parentNode.insertBefore(wrapper, input);
                wrapper.appendChild(input);
            }

            wrapper.querySelectorAll(
                ".tam-password-toggle"
            ).forEach((oldButton) => oldButton.remove());

            input.dataset.tamVisibilityReady = "2";

            const button = document.createElement("button");

            button.type = "button";
            button.className = "tam-password-toggle";

            // Password is hidden initially.
            input.type = "password";
            button.innerHTML = eyeClosed;

            button.setAttribute(
                "aria-label",
                "Show password"
            );

            button.setAttribute(
                "aria-pressed",
                "false"
            );

            button.title = "Show password";

            button.addEventListener("click", function () {
                const show = input.type === "password";

                input.type = show ? "text" : "password";

                // Open eye while password is visible.
                button.innerHTML = show
                    ? eyeOpen
                    : eyeClosed;

                button.setAttribute(
                    "aria-label",
                    show ? "Hide password" : "Show password"
                );

                button.setAttribute(
                    "aria-pressed",
                    String(show)
                );

                button.title = show
                    ? "Hide password"
                    : "Show password";
            });

            wrapper.appendChild(button);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener(
            "DOMContentLoaded",
            initializePasswordVisibility
        );
    } else {
        initializePasswordVisibility();
    }
})();
