/*
 * TAM Smart Photos — Browser Push Subscription
 *
 * Permission is requested only after an explicit button click.
 * Never expose private VAPID keys in browser JavaScript.
 */

(function () {
    "use strict";

    function base64UrlToUint8Array(value) {
        const padding = "=".repeat((4 - value.length % 4) % 4);

        const base64 = (value + padding)
            .replace(/-/g, "+")
            .replace(/_/g, "/");

        const raw = window.atob(base64);

        return Uint8Array.from(
            raw,
            character => character.charCodeAt(0)
        );
    }

    async function enableNotifications(button) {
        const status = document.getElementById(
            "tam-push-status"
        );

        function updateStatus(message) {
            if (status) {
                status.textContent = message;
            }
        }

        if (
            !("serviceWorker" in navigator) ||
            !("PushManager" in window) ||
            !("Notification" in window)
        ) {
            updateStatus(
                "Push notifications are not supported in this browser."
            );
            return;
        }

        button.disabled = true;
        updateStatus("Enabling notifications...");

        try {
            const permission = await Notification.requestPermission();

            if (permission !== "granted") {
                updateStatus(
                    "Notification permission was not granted."
                );
                return;
            }

            const keyResponse = await fetch(
                "/api/push/public-key",
                { credentials: "same-origin" }
            );

            if (!keyResponse.ok) {
                throw new Error("Unable to retrieve notification key.");
            }

            const keyData = await keyResponse.json();

            const registration = await navigator.serviceWorker.ready;

            let subscription =
                await registration.pushManager.getSubscription();

            if (!subscription) {
                subscription =
                    await registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey:
                            base64UrlToUint8Array(keyData.publicKey)
                    });
            }

            const saveResponse = await fetch(
                "/api/push/subscribe",
                {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        recipient_type: "admin",
                        endpoint: subscription.endpoint,
                        keys: subscription.toJSON().keys
                    })
                }
            );

            if (!saveResponse.ok) {
                if (saveResponse.status === 401) {
                    throw new Error(
                        "Please log in again before enabling notifications."
                    );
                }

                if (saveResponse.status === 403) {
                    throw new Error(
                        "Your account cannot enable Admin notifications."
                    );
                }

                if (saveResponse.status === 409) {
                    throw new Error(
                        "This browser is already linked to another account."
                    );
                }

                throw new Error(
                    "Unable to save your notification subscription."
                );
            }

            updateStatus("Notifications enabled successfully.");
            button.textContent = "Notifications Enabled";

        } catch (error) {
            console.error(
                "TAM notification subscription failed:",
                error.message
            );

            updateStatus(
                error.message || "Unable to enable notifications."
            );

        } finally {
            button.disabled = false;
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        const button = document.getElementById(
            "tam-enable-notifications"
        );

        if (!button) {
            return;
        }

        button.addEventListener("click", function () {
            enableNotifications(button);
        });
    });

})();
