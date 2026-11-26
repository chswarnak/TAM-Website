
/* TAM-GLOBAL-APPROVAL-V1 */

(function () {
    "use strict";

    let requests = [];
    let current = null;
    let csrfToken = "";
    let busy = false;

    // Later suppresses the same request on this page only.
    // Navigating to another Admin page allows it to appear again.
    // TAM-APPROVAL-SNOOZE-60S-V1
    const REMINDER_MS = 60000;
    const STORAGE_KEY = "tam-approval-snoozed-until-v1";

    const snoozed = new Map();

    try {
        const saved = JSON.parse(
            sessionStorage.getItem(STORAGE_KEY) || "{}"
        );

        for (const [id, until] of Object.entries(saved)) {
            if (Number.isFinite(until) && until > Date.now()) {
                snoozed.set(Number(id), until);
            }
        }
    } catch (error) {
        console.warn("Approval reminder storage unavailable.");
    }

    function saveSnoozed() {
        try {
            sessionStorage.setItem(
                STORAGE_KEY,
                JSON.stringify(
                    Object.fromEntries(snoozed)
                )
            );
        } catch (error) {
            // Storage may be unavailable.
        }
    }

    function createPopup() {
        const overlay = document.createElement("div");
        overlay.id = "tam-global-approval";
        overlay.setAttribute("role", "dialog");
        overlay.setAttribute("aria-modal", "true");
        overlay.setAttribute(
            "aria-labelledby",
            "tam-global-title"
        );

        overlay.innerHTML = `
          <div class="tam-approval-card">
            <h2 id="tam-global-title">
              🔔 Contributor Approval Request
            </h2>

            <div class="tam-approval-info">
              <strong>Full Name:</strong>
              <span id="tam-global-name"></span><br>

              <strong>Mobile:</strong>
              <span id="tam-global-mobile"></span><br>

              <strong>Status:</strong>
              Pending Approval
            </div>

            <div class="tam-approval-actions">
              <button type="button" id="tam-global-approve">
                ✅ Approve
              </button>

              <button type="button" id="tam-global-reject">
                ❌ Reject
              </button>

              <button type="button" id="tam-global-later">
                Later
              </button>
            </div>

            <p id="tam-global-error" role="alert"></p>
          </div>
        `;

        document.body.appendChild(overlay);
        return overlay;
    }

    const overlay = createPopup();

    const nameElement =
        document.getElementById("tam-global-name");

    const mobileElement =
        document.getElementById("tam-global-mobile");

    const errorElement =
        document.getElementById("tam-global-error");

    const approveButton =
        document.getElementById("tam-global-approve");

    // TAM-DIRECT-REJECT-V1
    const rejectButton =
        document.getElementById("tam-global-reject");

    function hide() {
        overlay.classList.remove("tam-visible");
        current = null;
    }

    function showNext() {
        if (busy || current) return;

        const next = requests.find(
            person =>
                !snoozed.has(person.id) ||
                Date.now() >= snoozed.get(person.id)
        );

        if (!next) return;

        current = next;
        nameElement.textContent = next.name || "Unknown";
        mobileElement.textContent =
            next.mobile_masked || "Not provided";

        errorElement.textContent = "";
        overlay.classList.add("tam-visible");
        approveButton.focus();
    }

    async function checkPending() {
        if (busy) return;

        try {
            const response = await fetch(
                "/admin/notifications/pending-contributors",
                {
                    credentials: "same-origin",
                    cache: "no-store"
                }
            );

            if (response.status === 401 ||
                response.status === 403) {
                hide();
                requests = [];
                return;
            }

            if (!response.ok) return;

            const data = await response.json();

            requests = data.contributors || [];
            csrfToken = data.csrf_token || "";

            if (current && !requests.some(
                person => person.id === current.id
            )) {
                hide();
            }

            showNext();

        } catch (error) {
            console.error(
                "TAM Contributor approval check failed."
            );
        }
    }

    approveButton.addEventListener("click", async () => {
        if (!current || !csrfToken || busy) return;

        busy = true;
        approveButton.disabled = true;
        errorElement.textContent = "";

        const contributorId = current.id;

        try {
            const body = new URLSearchParams();
            body.set("csrf_token", csrfToken);

            const response = await fetch(
                "/admin/approve/" +
                encodeURIComponent(contributorId),
                {
                    method: "POST",
                    credentials: "same-origin",
                    body: body
                }
            );

            if (!response.ok) {
                throw new Error(
                    "Approval failed. Please review the request."
                );
            }

            hide();
            requests = requests.filter(
                person => person.id !== contributorId
            );

            await checkPending();

        } catch (error) {
            errorElement.textContent = error.message;
        } finally {
            busy = false;
            approveButton.disabled = false;
        }
    });

    // TAM-DIRECT-REJECT-V1
    rejectButton.addEventListener("click", async () => {
        if (!current || busy) return;

        if (!csrfToken) {
            errorElement.textContent =
                "Security token unavailable. Refresh the page.";
            return;
        }

        const contributorId = current.id;
        const contributorName = current.name || "this contributor";

        const confirmed = window.confirm(
            "Reject Contributor: " + contributorName +
            "?\\n\\nThis will disable their registration " +
            "and prevent them from uploading photographs."
        );

        if (!confirmed) return;

        busy = true;
        approveButton.disabled = true;
        rejectButton.disabled = true;
        errorElement.textContent = "";

        try {
            const body = new URLSearchParams();
            body.set("csrf_token", csrfToken);

            const response = await fetch(
                "/admin/reject/" +
                encodeURIComponent(contributorId),
                {
                    method: "POST",
                    credentials: "same-origin",
                    cache: "no-store",
                    redirect: "error",
                    body: body
                }
            );

            if (!response.ok) {
                if (response.status === 409) {
                    throw new Error(
                        "This request has already been processed."
                    );
                }

                if (
                    response.status === 401 ||
                    response.status === 403
                ) {
                    throw new Error(
                        "Admin authorization failed. Please log in again."
                    );
                }

                throw new Error(
                    "Unable to reject this Contributor."
                );
            }

            const result = await response.json();

            if (
                result.status !== "rejected" ||
                result.contributor_id !== contributorId
            ) {
                throw new Error(
                    "Unexpected rejection response."
                );
            }

            requests = requests.filter(
                person => person.id !== contributorId
            );

            snoozed.delete(contributorId);
            saveSnoozed();

            hide();

        } catch (error) {
            errorElement.textContent =
                error.message || "Rejection failed.";

        } finally {
            busy = false;
            approveButton.disabled = false;
            rejectButton.disabled = false;
        }

        if (!current) {
            await checkPending();
        }
    });

    document.getElementById("tam-global-later")
        .addEventListener("click", () => {
            if (current) {
                snoozed.set(
                    current.id,
                    Date.now() + REMINDER_MS
                );
                saveSnoozed();
            }

            hide();
            showNext();
        });

    // Poll only while the Admin page is visible.
    setInterval(() => {
        if (!document.hidden) {
            checkPending();
        }
    }, 10000);

    document.addEventListener(
        "visibilitychange",
        () => {
            if (!document.hidden) {
                checkPending();
            }
        }
    );

    checkPending();
})();
