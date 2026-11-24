// TAM-GRAND-SPARKLES-V2

// TAM-GRAND-SPARKLES-V1
(() => {
    "use strict";

    const reducedMotion = window.matchMedia(
        "(prefers-reduced-motion: reduce)"
    );

    const selectors = [
        ".portal a.option",
        ".container a.option",
        "a.tam-gallery-back",
        "a.tam-back-to-event",
        'a[href="/events/tam-diwali-2026"]',
        "button:not(#openCamera)",
        'input[type="submit"]'
    ];

    const targets = new Set();

    for (const selector of selectors) {
        document.querySelectorAll(selector).forEach(element => {
            targets.add(element);
        });
    }

    function burst(element) {
        if (reducedMotion.matches) return;

        const rect = element.getBoundingClientRect();

        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;

        const symbols = [
            "✨", "⭐", "🌟", "✦", "✧"
        ];

        const count = 32;

        for (let i = 0; i < count; i++) {
            const star = document.createElement("span");

            star.className = "tam-grand-particle";
            star.textContent = symbols[i % symbols.length];

            const angle = (Math.PI * 2 * i) / count;
            const distance = 145 + (i % 5) * 25;

            star.style.left = x + "px";
            star.style.top = y + "px";

            star.style.setProperty(
                "--dx",
                Math.cos(angle) * distance + "px"
            );

            star.style.setProperty(
                "--dy",
                Math.sin(angle) * distance + "px"
            );

            document.body.appendChild(star);

            setTimeout(() => star.remove(), 1500);
        }
    }

    let audioContext = null;

    function chime() {
        try {
            const Audio =
                window.AudioContext ||
                window.webkitAudioContext;

            if (!Audio) return;

            audioContext ??= new Audio();

            if (audioContext.state === "suspended") {
                audioContext.resume().catch(() => {});
            }

            const now = audioContext.currentTime;

            for (const [frequency, delay] of [
                [880, 0],
                [1175, .08],
                [1568, .16]
            ]) {
                const oscillator =
                    audioContext.createOscillator();

                const gain =
                    audioContext.createGain();

                oscillator.type = "sine";
                oscillator.frequency.value = frequency;

                gain.gain.setValueAtTime(
                    .0001, now + delay
                );

                gain.gain.exponentialRampToValueAtTime(
                    .035, now + delay + .02
                );

                gain.gain.exponentialRampToValueAtTime(
                    .0001, now + delay + .18
                );

                oscillator.connect(gain);
                gain.connect(audioContext.destination);

                oscillator.start(now + delay);
                oscillator.stop(now + delay + .19);
            }
        } catch {
            // Optional sound must never interrupt navigation.
        }
    }

    targets.forEach(element => {
        if (
            element.disabled ||
            element.getAttribute("aria-disabled") === "true"
        ) {
            return;
        }

        element.classList.add("tam-grand-target");

        element.addEventListener("click", event => {
            if (
                event.defaultPrevented ||
                (event.button !== undefined && event.button !== 0) ||
                event.ctrlKey ||
                event.metaKey ||
                event.altKey ||
                event.shiftKey
            ) {
                return;
            }

            burst(element);
            chime();

            // Allow the animation to become visible before
            // same-tab navigation. Do not delay form submissions.
            if (
                element.tagName !== "A" ||
                element.hasAttribute("download") ||
                (element.target && element.target !== "_self")
            ) {
                return;
            }

            const destination = element.href;

            if (!destination) return;

            event.preventDefault();

            setTimeout(() => {
                window.location.assign(destination);
            }, reducedMotion.matches ? 0 : 250);
        });
    });
})();
