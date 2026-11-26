
"use strict";

(() => {

    const MAX_PHOTOS = 4;
    const POLL_MS = 2000;

    const $ = id => document.getElementById(id);

    const grid = $("wall-grid");
    const stage = $("tam-tv-stage");

    let currentMode = null;
    let currentSignature = "";
    let slideLayer = 0;
    let slideId = null;
    let slideShownAt = 0;
    let idleIndex = 0;
    let polling = false;
    let lastState = null;
    let generation = 0;

    function validIds(values) {

        if (!Array.isArray(values)) return [];

        return [...new Set(values.map(Number))]
            .filter(id =>
                Number.isSafeInteger(id) && id > 0
            );
    }

    function imageUrl(id) {

        return `/api/tv/photos/${id}/image`;
    }

    function setMode(mode) {

        if (mode === "wall" &&
            !grid.querySelector("img")) {
            mode = "waiting";
        }

        if (mode === "slideshow") {

            const activeImage = stage.querySelector(
                ".slide-layer.is-active .slide-img"
            );

            if (!activeImage ||
                !activeImage.complete ||
                !activeImage.naturalWidth) {
                mode = "waiting";
            }
        }

        currentMode = mode;

        document.body.classList.toggle(
            "tv-blank",
            mode === "blank"
        );

        stage.querySelectorAll(".mode").forEach(section => {

            section.classList.toggle(
                "is-active",
                section.dataset.mode === mode
            );

        });
    }

    function clearPhotos() {

        grid.replaceChildren();

        document.querySelectorAll(".slide-layer")
            .forEach(layer => {

                layer.classList.remove("is-active");

                layer.querySelector(".slide-img")
                    .removeAttribute("src");

                layer.querySelector(".slide-fill")
                    .style.backgroundImage = "";

            });

        currentSignature = "";
        slideId = null;
        slideShownAt = 0;
    }

    function blankScreen() {

        generation += 1;

        clearPhotos();


        setMode("blank");
    }

    function preload(id) {

        return new Promise((resolve, reject) => {

            const image = new Image();

            const timer = setTimeout(() => {
                reject(new Error("Photo load timeout"));
            }, 8000);

            image.onload = () => {

                clearTimeout(timer);

                resolve(image);
            };

            image.onerror = () => {

                clearTimeout(timer);

                reject(new Error("Photo unavailable"));
            };

            image.src = imageUrl(id);
        });
    }

    async function renderWall(ids) {

        ids = validIds(ids).slice(0, MAX_PHOTOS);

        if (!ids.length) {

            clearPhotos();

            setMode("waiting");

            return;
        }

        const signature = ids.join(",");

        if (
            currentMode === "wall" &&
            currentSignature === signature
        ) {
            return;
        }

        // Preload the next authorized batch before switching.
        // Existing wall content is cleared when eligibility changes.

        if (currentMode === "wall") {
            clearPhotos();
        }

        const requestGeneration = ++generation;

        const results = await Promise.allSettled(
            ids.map(preload)
        );

        if (requestGeneration !== generation) return;

        const fragment = document.createDocumentFragment();

        let count = 0;

        for (const result of results) {

            if (result.status !== "fulfilled") continue;

            const figure = document.createElement("figure");

            figure.className = "tam-tv-photo";

            const image = result.value;

            image.alt = "TAM event photograph";

            figure.appendChild(image);

            fragment.appendChild(figure);

            count += 1;
        }

        if (!count) {

            setMode("waiting");

            return;
        }

        grid.dataset.count = String(count);

        grid.replaceChildren(fragment);

        currentSignature = signature;

        setMode("wall");
    }

    async function showSlide(id, kenBurns) {

        const requestGeneration = ++generation;

        let photo;

        try {

            photo = await preload(id);

        } catch {

            return;
        }

        if (requestGeneration !== generation) return;

        slideLayer = 1 - slideLayer;

        const incoming = $(
            slideLayer ? "slide-b" : "slide-a"
        );

        const outgoing = $(
            slideLayer ? "slide-a" : "slide-b"
        );

        const image = incoming.querySelector(
            ".slide-img"
        );

        for (let i = 1; i <= 4; i++) {

            incoming.classList.remove(`kb-${i}`);
        }

        incoming.querySelector(
            ".slide-fill"
        ).style.backgroundImage =
            `url("${photo.src}")`;

        image.src = photo.src;

        void incoming.offsetWidth;

        if (kenBurns) {

            const variant =
                1 + Math.floor(Math.random() * 4);

            incoming.classList.add(`kb-${variant}`);
        }

        incoming.classList.add("is-active");

        outgoing.classList.remove("is-active");

        $("slide-empty").hidden = true;

        slideId = id;

        slideShownAt = Date.now();

        setMode("slideshow");
    }

    async function renderIdle(state) {

        const ids = validIds(state.idle_photos);

        if (!ids.length) {

            clearPhotos();

            setMode("waiting");

            return;
        }

        const seconds = Math.min(
            60,
            Math.max(
                5,
                Number(state.slideshow_duration_seconds) || 10
            )
        );

        const due =
            Date.now() - slideShownAt >= seconds * 1000;

        const stillAuthorized = ids.includes(slideId);

        if (
            currentMode !== "slideshow" ||
            !stillAuthorized ||
            due
        ) {

            if (!stillAuthorized) {

                clearPhotos();

                setMode("waiting");
            }

            idleIndex = idleIndex % ids.length;

            const nextId = ids[idleIndex];

            idleIndex += 1;

            await showSlide(
                nextId,
                state.ken_burns_enabled !== false
            );
        }
    }

    async function applyState(state) {

        lastState = state;

        if (
            state.enabled !== true ||
            state.mode === "blank"
        ) {

            blankScreen();

            return;
        }

        document.body.classList.toggle(
            "tv-no-kenburns",
            state.ken_burns_enabled === false
        );

        if (state.mode === "paused") {

            generation += 1;

            clearPhotos();

            setMode("waiting");

            return;
        }

        if (state.mode === "photo_wall") {

            if (
                currentMode === "blank" ||
                (currentMode === "wall" &&
                 !grid.querySelector("img"))
            ) {
                setMode("waiting");
            }

            await renderWall(state.photos);

            return;
        }

        if (state.mode === "slideshow") {

            if (currentMode === "blank") {
                setMode("waiting");
            }

            await renderIdle(state);

            return;
        }

        blankScreen();
    }

    async function poll() {

        if (polling) return;

        polling = true;

        const controller = new AbortController();

        const timeout = setTimeout(
            () => controller.abort(),
            5000
        );

        try {

            const response = await fetch(
                "/api/tv/state",
                {
                    credentials: "same-origin",
                    cache: "no-store",
                    signal: controller.signal
                }
            );

            if (!response.ok) {

                throw new Error("TV state unavailable");
            }

            const state = await response.json();

            await applyState(state);

        } catch (error) {

            blankScreen();

            console.warn(
                "TAM Live TV:",
                error.message
            );

        } finally {

            clearTimeout(timeout);

            polling = false;
        }
    }

    async function toggleFullscreen() {

        try {

            if (document.fullscreenElement) {

                await document.exitFullscreen();

            } else {

                await document.documentElement
                    .requestFullscreen();
            }

        } catch (error) {

            console.warn(
                "Fullscreen unavailable:",
                error.message
            );
        }
    }

    $("tam-tv-fullscreen").addEventListener(
        "click",
        toggleFullscreen
    );

    document.addEventListener(
        "keydown",
        event => {

            if (event.key.toLowerCase() === "f") {

                toggleFullscreen();
            }
        }
    );

    document.addEventListener(
        "visibilitychange",
        () => {

            if (!document.hidden) poll();
        }
    );

    blankScreen();

    poll();

    setInterval(poll, POLL_MS);

})();
