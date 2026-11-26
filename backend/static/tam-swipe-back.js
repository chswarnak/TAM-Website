
// TAM-MOBILE-SWIPE-BACK-V1

(() => {
    "use strict";

    const config = document.querySelector(
        'script[data-tam-swipe-back]'
    );

    if (!config) return;

    const destination = config.dataset.tamSwipeBack;

    if (!destination || !destination.startsWith("/") ||
        destination.startsWith("//")) {
        return;
    }

    // Mobile touch devices only.
    if (!window.matchMedia(
        "(pointer: coarse) and (max-width: 900px)"
    ).matches) {
        return;
    }

    const EDGE_WIDTH = 32;
    const MIN_DISTANCE = 100;
    const MAX_VERTICAL = 65;
    const MAX_DURATION = 900;

    let startX = 0;
    let startY = 0;
    let startTime = 0;
    let tracking = false;
    let startingTarget = null;

    const ignored = [
        "input",
        "textarea",
        "select",
        "button",
        "a",
        "video",
        "canvas",
        "[contenteditable]",
        "[role='slider']"
    ].join(",");

    document.addEventListener(
        "touchstart",
        event => {
            tracking = false;
            startingTarget = null;

            if (event.touches.length !== 1) return;

            const touch = event.touches[0];

            if (touch.clientX > EDGE_WIDTH) return;

            const target = event.target;

            if (target instanceof Element &&
                target.closest(ignored)) {
                return;
            }

            startX = touch.clientX;
            startY = touch.clientY;
            startTime = Date.now();
            startingTarget = target;
            tracking = true;
        },
        { passive: true }
    );

    document.addEventListener(
        "touchcancel",
        () => {
            tracking = false;
        },
        { passive: true }
    );

    document.addEventListener(
        "touchend",
        event => {
            if (!tracking) return;

            tracking = false;

            if (event.changedTouches.length !== 1) {
                return;
            }

            const touch = event.changedTouches[0];

            const dx = touch.clientX - startX;
            const dy = touch.clientY - startY;
            const duration = Date.now() - startTime;

            if (
                dx < MIN_DISTANCE ||
                Math.abs(dy) > MAX_VERTICAL ||
                duration > MAX_DURATION
            ) {
                return;
            }

            // Don't navigate if another interaction
            // has taken over the gesture.
            if (
                startingTarget instanceof Element &&
                !startingTarget.isConnected
            ) {
                return;
            }

            // Use an explicit destination instead of
            // unrestricted browser history navigation.
            // Server-side consent/auth checks remain active.
            window.location.assign(destination);
        },
        { passive: true }
    );
})();
