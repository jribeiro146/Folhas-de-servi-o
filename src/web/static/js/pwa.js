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

    const showUpdateNotice = () => {
        const workspace = document.querySelector(".workspace");
        if (!workspace || document.getElementById("pwa-update-notice")) return;
        const notice = document.createElement("section");
        notice.id = "pwa-update-notice";
        notice.className = "pwa-update-notice";
        notice.setAttribute("role", "status");
        notice.textContent = "Está disponível uma nova versão. Confirme que o trabalho ficou guardado no servidor; " +
            "depois feche todas as abas e janelas desta aplicação e volte a abri-la para atualizar.";
        workspace.prepend(notice);
    };
    const observeUpdate = registration => {
        if (registration.waiting) showUpdateNotice();
        registration.addEventListener("updatefound", () => {
            const candidate = registration.installing;
            candidate?.addEventListener("statechange", () => {
                if (candidate.state === "installed" && registration.active) showUpdateNotice();
            });
        });
        // Deliberately no skipWaiting message or controllerchange reload:
        // installing a release must not discard a form or pending photographs.
        return registration;
    };
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/service-worker.js", {
            scope: "/",
            updateViaCache: "none"
        }).then(observeUpdate).then((registration) => registration.update()).catch(() => {});
        showIOSInstallControls();
        showInstallControls();
    });
})();
