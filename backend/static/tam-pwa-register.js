'use strict';

// TAM Smart Photos PWA registration.
// This registers the service worker without requesting
// notification permission automatically.

(function () {
    if (!('serviceWorker' in navigator)) {
        return;
    }

    window.addEventListener('load', async function () {
        try {
            const registration = await navigator.serviceWorker.register(
                '/tam-sw.js',
                { scope: '/' }
            );

            console.log(
                '[TAM PWA] Service worker registered:',
                registration.scope
            );
        } catch (error) {
            console.error(
                '[TAM PWA] Service worker registration failed:',
                error
            );
        }
    });
})();
