(() => {
    let deferredInstallPrompt = null;

    const installButtons = () => Array.from(document.querySelectorAll("[data-pwa-install]"));
    const installCopies = () => Array.from(document.querySelectorAll("[data-pwa-install-copy]"));
    const iosInstallCopies = () => Array.from(document.querySelectorAll("[data-pwa-ios-install]"));

    const isStandalone = () => (
        window.matchMedia?.("(display-mode: standalone)")?.matches ||
        window.navigator.standalone === true
    );

    const isIOS = () => (
        /iphone|ipad|ipod/i.test(window.navigator.userAgent || "") ||
        (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1)
    );

    const showIOSInstallControls = () => {
        if (!isIOS() || isStandalone()) {
            return;
        }

        iosInstallCopies().forEach((copy) => {
            copy.hidden = false;
        });
    };

    const showInstallControls = () => {
        if (!deferredInstallPrompt || isStandalone()) {
            return;
        }

        installButtons().forEach((button) => {
            button.hidden = false;
            button.disabled = false;
        });
        installCopies().forEach((copy) => {
            copy.hidden = false;
        });
    };

    const hideInstallControls = () => {
        installButtons().forEach((button) => {
            button.hidden = true;
        });
        installCopies().forEach((copy) => {
            copy.hidden = true;
        });
        iosInstallCopies().forEach((copy) => {
            copy.hidden = true;
        });
    };

    window.addEventListener("beforeinstallprompt", (event) => {
        event.preventDefault();
        deferredInstallPrompt = event;
        showInstallControls();
    });

    window.addEventListener("appinstalled", () => {
        deferredInstallPrompt = null;
        hideInstallControls();
    });

    document.addEventListener("click", async (event) => {
        const button = event.target.closest?.("[data-pwa-install]");
        if (!button || !deferredInstallPrompt) {
            return;
        }

        button.disabled = true;
        try {
            deferredInstallPrompt.prompt();
            await deferredInstallPrompt.userChoice;
        } catch (error) {
            // O Chrome pode bloquear o prompt se o evento já tiver sido consumido.
        } finally {
            deferredInstallPrompt = null;
            hideInstallControls();
        }
    });

    if (!("serviceWorker" in navigator)) {
        return;
    }

    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/service-worker.js", {
            scope: "/",
            updateViaCache: "none"
        }).then((registration) => registration.update()).catch(() => {});
        showIOSInstallControls();
        showInstallControls();
    });
})();
