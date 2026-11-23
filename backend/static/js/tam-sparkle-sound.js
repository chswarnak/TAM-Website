/* TAM FESTIVE SPARKLE SOUND V1
   Gentle button-click chime.
   No external audio files required.
   No network requests or tracking.
*/

(function () {
    "use strict";

    const STORAGE_KEY = "tam_sparkle_sound_enabled";

    let audioContext = null;

    function soundEnabled() {
        try {
            return localStorage.getItem(STORAGE_KEY) !== "false";
        } catch (_) {
            return true;
        }
    }

    function setSoundEnabled(enabled) {
        try {
            localStorage.setItem(
                STORAGE_KEY,
                enabled ? "true" : "false"
            );
        } catch (_) {
            // Sound preference remains session-only if storage is blocked.
        }
    }

    function playSparkle() {
        if (!soundEnabled()) return;

        try {
            const AudioContextClass =
                window.AudioContext || window.webkitAudioContext;

            if (!AudioContextClass) return;

            if (!audioContext) {
                audioContext = new AudioContextClass();
            }

            if (audioContext.state === "suspended") {
                audioContext.resume().catch(() => {});
            }

            const now = audioContext.currentTime;

            const notes = [
                { frequency: 880, delay: 0 },
                { frequency: 1320, delay: 0.075 }
            ];

            for (const note of notes) {
                const oscillator = audioContext.createOscillator();
                const gain = audioContext.createGain();

                oscillator.type = "sine";
                oscillator.frequency.value = note.frequency;

                const start = now + note.delay;
                const end = start + 0.20;

                gain.gain.setValueAtTime(0.0001, start);
                gain.gain.exponentialRampToValueAtTime(
                    0.035,
                    start + 0.015
                );
                gain.gain.exponentialRampToValueAtTime(
                    0.0001,
                    end
                );

                oscillator.connect(gain);
                gain.connect(audioContext.destination);

                oscillator.start(start);
                oscillator.stop(end + 0.01);
            }
        } catch (_) {
            // Audio failures must never interrupt website functions.
        }
    }

    // Public functions for future Sound On/Off buttons.
    window.TAMSound = {
        play: playSparkle,
        enabled: soundEnabled,
        setEnabled: setSoundEnabled
    };

    // Listen for normal user-initiated clicks.
    document.addEventListener("click", function (event) {
        const target = event.target instanceof Element
            ? event.target.closest(
                "button, a, input[type='submit'], input[type='button']"
            )
            : null;

        if (!target || target.disabled ||
            target.getAttribute("aria-disabled") === "true") {
            return;
        }

        // Avoid playing during modified clicks and middle-clicks.
        if (event.button !== 0 ||
            event.ctrlKey || event.metaKey ||
            event.altKey || event.shiftKey) {
            return;
        }

        playSparkle();
    });
})();
