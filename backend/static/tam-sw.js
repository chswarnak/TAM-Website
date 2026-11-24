'use strict';

// TAM Smart Photos — Push Notification Service Worker.
// Version: 1.0

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// Receive push notifications from the TAM backend.
self.addEventListener('push', (event) => {

    let payload = {};

    try {
        payload = event.data ? event.data.json() : {};
    } catch (error) {
        payload = {};
    }

    const title =
        typeof payload.title === 'string'
            ? payload.title
            : 'TAM Smart Photos';

    const options = {
        body:
            typeof payload.body === 'string'
                ? payload.body
                : 'You have a new notification.',

        icon: '/static/icons/tam-icon-192.png',

        badge: '/static/icons/tam-icon-192.png',

        tag:
            typeof payload.tag === 'string'
                ? payload.tag
                : 'tam-notification',

        data: {
            url:
                typeof payload.url === 'string' &&
                payload.url.startsWith('/') &&
                !payload.url.startsWith('//')
                    ? payload.url
                    : '/'
        }
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// Open the appropriate TAM page when a notification is tapped.
self.addEventListener('notificationclick', (event) => {

    event.notification.close();

    const path = event.notification.data?.url || '/';

    const destination = new URL(path, self.location.origin);

    if (destination.origin !== self.location.origin) {
        return;
    }

    event.waitUntil(
        self.clients.openWindow(destination.href)
    );
});
