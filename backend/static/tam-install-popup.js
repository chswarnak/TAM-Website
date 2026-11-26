
/* TAM-PWA-INSTALL-POPUP-V1 */
(function () {
    "use strict";

    var deferredPrompt = null;
    var storageKey = "tam-pwa-install-dismissed-v1";

    function alreadyInstalled() {
        return window.matchMedia("(display-mode: standalone)").matches ||
            window.navigator.standalone === true;
    }

    function dismissed() {
        try {
            return localStorage.getItem(storageKey) === "yes";
        } catch (error) {
            return false;
        }
    }

    function rememberDismissal() {
        try {
            localStorage.setItem(storageKey, "yes");
        } catch (error) {
            // Storage may be unavailable.
        }
    }

    function showPopup() {
        var overlay = document.getElementById("tam-install-overlay");
        if (!overlay || alreadyInstalled() || dismissed()) {
            return;
        }
        overlay.classList.add("tam-install-visible");
    }

    function hidePopup() {
        var overlay = document.getElementById("tam-install-overlay");
        if (overlay) {
            overlay.classList.remove("tam-install-visible");
        }
    }

    window.addEventListener("beforeinstallprompt", function (event) {
        event.preventDefault();
        deferredPrompt = event;
    });

    window.addEventListener("appinstalled", function () {
        deferredPrompt = null;
        rememberDismissal();
        hidePopup();
    });

    document.addEventListener("DOMContentLoaded", function () {
        var install = document.getElementById("tam-install-now");
        var skip = document.getElementById("tam-install-skip");
        var instructions =
            document.getElementById("tam-install-instructions");

        if (!install || !skip || !instructions) {
            return;
        }

        var ua = navigator.userAgent || "";
        var isIOS = /iPhone|iPad|iPod/i.test(ua) ||
            (navigator.platform === "MacIntel" &&
             navigator.maxTouchPoints > 1);

        install.addEventListener("click", async function () {
            if (deferredPrompt) {
                deferredPrompt.prompt();

                try {
                    await deferredPrompt.userChoice;
                } catch (error) {
                    console.log("Installation choice unavailable.");
                }

                deferredPrompt = null;
                return;
            }

            instructions.hidden = false;

            if (isIOS) {
                instructions.textContent =
                    "On iPhone: open this page in Safari, tap Share, " +
                    "then Add to Home Screen.";
            } else {
                instructions.textContent =
                    "Open your browser menu and select Install app " +
                    "or Add to Home Screen.";
            }
        });

        skip.addEventListener("click", function () {
            rememberDismissal();
            hidePopup();
        });

        if (
            /Android|iPhone|iPad|iPod/i.test(ua) &&
            !alreadyInstalled() &&
            !dismissed()
        ) {
            window.setTimeout(showPopup, 900);
        }
    });
})();
