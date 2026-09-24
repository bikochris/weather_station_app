function showAuthSection(sectionId) {
    document.getElementById("loginSection").hidden = sectionId !== "loginSection";
    document.getElementById("setupSection").hidden = sectionId !== "setupSection";
}


async function loadAuthState() {
    if (getAccessToken()) {
        const response = await apiFetch("/auth/me");
        if (response.ok) {
            window.location.href = "index.html";
            return;
        }
    }

    try {
        const response = await fetch(`${API_URL}/auth/status`);
        if (!response.ok) {
            throw new Error(await getErrorMessage(response, "Unable to check setup status"));
        }
        const status = await response.json();
        showAuthSection(status.needs_setup ? "setupSection" : "loginSection");
    } catch (error) {
        showAuthSection("loginSection");
        setMessage(document.getElementById("loginMessage"), error.message, "error");
    }
}


document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("loginForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = document.getElementById("loginMessage");
        setMessage(message, "Signing in...");

        try {
            const response = await fetch(`${API_URL}/auth/login`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    username: document.getElementById("loginUsername").value.trim(),
                    password: document.getElementById("loginPassword").value
                })
            });
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to sign in"));
            }
            const result = await response.json();
            saveAccessToken(result.access_token);
            window.location.href = "index.html";
        } catch (error) {
            setMessage(message, error.message, "error");
        }
    });

    document.getElementById("setupForm").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = document.getElementById("setupMessage");
        const password = document.getElementById("setupPassword").value;
        const confirmation = document.getElementById("setupPasswordConfirm").value;

        if (password !== confirmation) {
            setMessage(message, "Passwords do not match.", "error");
            return;
        }

        setMessage(message, "Creating administrator...");
        try {
            const response = await fetch(`${API_URL}/auth/bootstrap`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    full_name: document.getElementById("setupFullName").value.trim(),
                    username: document.getElementById("setupUsername").value.trim(),
                    email: document.getElementById("setupEmail").value.trim() || null,
                    password
                })
            });
            if (!response.ok) {
                throw new Error(await getErrorMessage(response, "Unable to create administrator"));
            }
            document.getElementById("loginUsername").value =
                document.getElementById("setupUsername").value.trim();
            showAuthSection("loginSection");
            setMessage(
                document.getElementById("loginMessage"),
                "Administrator created. Sign in to continue.",
                "success"
            );
        } catch (error) {
            setMessage(message, error.message, "error");
        }
    });

    loadAuthState();
});
