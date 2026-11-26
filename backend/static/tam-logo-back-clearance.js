
/* TAM-LOGO-BACK-CLEARANCE-V1 */

(function () {
    "use strict";

    const GAP = 14;
    const originals = new WeakMap();

    function isBackButton(element) {
        const text = (
            element.textContent || ""
        ).replace(/\s+/g, " ").trim();

        return /^(?:←|↩)?\s*(?:Back to|Return to)\b/i.test(text);
    }

    function overlaps(a, b) {
        return (
            a.left < b.right &&
            a.right > b.left &&
            a.top < b.bottom &&
            a.bottom > b.top
        );
    }

    function adjust() {
        const logo = document.querySelector("a.tam-site-logo");

        if (!logo) return;

        const logoRect = logo.getBoundingClientRect();

        const protectedRect = {
            left: logoRect.left - GAP,
            right: logoRect.right + GAP,
            top: logoRect.top - GAP,
            bottom: logoRect.bottom + GAP
        };

        const candidates = document.querySelectorAll(
            "a[href], button"
        );

        candidates.forEach(function (button) {
            if (!isBackButton(button)) return;

            if (!originals.has(button)) {
                originals.set(button, {
                    translate: button.style.translate
                });
            }

            // Measure the original position on every resize.
            button.style.translate =
                originals.get(button).translate;

            const rect = button.getBoundingClientRect();

            if (!overlaps(rect, protectedRect)) {
                return;
            }

            const moveRight = Math.ceil(
                protectedRect.right - rect.left
            );

            const availableRight =
                window.innerWidth - rect.right - GAP;

            if (moveRight <= availableRight) {
                button.style.translate =
                    moveRight + "px 0px";
            } else {
                const moveDown = Math.ceil(
                    protectedRect.bottom - rect.top
                );

                button.style.translate =
                    "0px " + moveDown + "px";
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", adjust);
    } else {
        adjust();
    }

    window.addEventListener("load", adjust);
    window.addEventListener("resize", adjust);

})();
