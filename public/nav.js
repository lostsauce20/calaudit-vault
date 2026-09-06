(function () {
    const CSS = `
    #calaudit-nav {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 9999;
    background: #0d0d0d;
    border-bottom: 1px solid #2a2a2a;
    font-family: 'Courier New', 'Lucida Console', monospace;
    font-size: 12px;
    }
    #calaudit-nav .nav-inner {
    max-width: 1250px;
    margin: 0 auto;
    padding: 0 2rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    }
    @media (max-width: 1300px) {
        #calaudit-nav .nav-inner {
        padding: 0 1rem;
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
        }
    }
    #calaudit-nav .nav-row {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    }
    #calaudit-nav .nav-row + .nav-row {
    border-top: 1px solid #1a1a1a;
    }
    #calaudit-nav .nav-logo {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.75rem 0 0.25rem 0;
    color: #d85a30;
    text-decoration: none;
    font-weight: bold;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    font-size: 12.5px;
    flex-shrink: 0;
    }
    #calaudit-nav .nav-logo:hover {
    color: #e87040;
    text-decoration: none;
    }
    #calaudit-nav .nav-logo .logo-bracket {
    color: #555550;
    font-size: 16px;
    font-weight: normal;
    }
    #calaudit-nav .nav-links {
    display: flex;
    align-items: stretch;
    list-style: none;
    margin: 0;
    padding: 0;
    gap: 0;
    flex-wrap: wrap;
    justify-content: center;
    }
    #calaudit-nav .nav-links li {
    display: flex;
    align-items: stretch;
    }
    #calaudit-nav .nav-links a {
    display: flex;
    align-items: center;
    padding: 0.25rem 0.5rem;
    color: #c5a059;
    text-decoration: none;
    letter-spacing: 0.06em;
    text-transform: none;
    font-size: 11.2px;
    border-left: 1px solid #1e1e1e;
    transition: color 0.15s, background 0.15s;
    white-space: nowrap;
    }
    #calaudit-nav .nav-links li:first-child a {
    border-left: none;
    }
    #calaudit-nav .nav-links a:hover {
    color: #f0d59e;
    background: #161616;
    text-decoration: none;
    }
    #calaudit-nav .nav-links a.active {
    color: #d85a30;
    border-bottom: 2px solid #d85a30;
    }
    #calaudit-nav .nav-row-2 .nav-links a {
    color: #7a9e7e;
    }
    #calaudit-nav .nav-row-2 .nav-links a:hover {
    color: #a8c8a8;
    background: #161616;
    }
    #calaudit-nav .nav-row-2 .nav-links a.active {
    color: #5a9e5a;
    border-bottom: 2px solid #5a9e5a;
    }
    #calaudit-nav .nav-mobile-menu {
    display: none;
    }
    #calaudit-nav .nav-hamburger {
    display: none;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    cursor: pointer;
    color: #c5a059;
    font-size: 20px;
    padding: 0 0.5rem;
    min-width: 44px;
    min-height: 44px;
    line-height: 1;
    }
    #calaudit-nav .nav-hamburger:hover {
    color: #f0d59e;
    }
    @media (max-width: 1300px) {
        #calaudit-nav .nav-row {
        display: none;
        }
        #calaudit-nav .nav-hamburger {
        display: flex;
        }
        #calaudit-nav .nav-mobile-menu {
        display: none;
        flex-direction: column;
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        background: #0d0d0d;
        border-bottom: 1px solid #2a2a2a;
        max-height: calc(100vh - 60px);
        overflow-y: auto;
        list-style: none;
        margin: 0;
        padding: 0;
        z-index: 9999;
        }
        #calaudit-nav .nav-mobile-menu.open {
        display: flex;
        }
        #calaudit-nav .nav-mobile-menu li {
        border-top: 1px solid #1e1e1e;
        }
        #calaudit-nav .nav-mobile-menu a {
        display: flex;
        align-items: center;
        padding: 0.85rem 2rem;
        color: #c5a059;
        text-decoration: none;
        letter-spacing: 0.06em;
        font-size: 12px;
        transition: color 0.15s, background 0.15s;
        }
        #calaudit-nav .nav-mobile-menu a:hover {
        color: #f0d59e;
        background: #161616;
        }
        #calaudit-nav .nav-mobile-menu a.active {
        border-left: 2px solid #d85a30;
        color: #d85a30;
        }
        #calaudit-nav .nav-mobile-menu a.active.row2 {
        border-left-color: #5a9e5a;
        color: #5a9e5a;
        }
        #calaudit-nav .nav-mobile-menu a.row2 {
        color: #7a9e7e;
        }
    }
    `;

    const LINKS_ROW1 = [
        { label: "Records", href: "/forensic-vault/" },
        { label: "Privacy Tools", href: "/tools/" },
        { label: "Dope Forms", href: "/dope-forms/" },
        { label: "Dead 1/4s", href: "/deadquarters/" },
        { label: "DHC-$$$", href: "/dhcs4521/" },
        { label: "Knox-Keene", href: "/knox-keene/" },
        { label: "DMHC", href: "/dmhc-faqs/" },
        { label: "No Meds", href: "/medical-vault/" },
        { label: "Law/Order?", href: "/litigation/" },
        { label: "Hello!", href: "/telecom/" },
    ];

    const LINKS_ROW2 = [
        { label: "SSDD", href: "/advocate/" },
        { label: "Receipts", href: "/evidence-room/" },
        { label: "Living Proof", href: "/medical-abandonment/" },
        { label: "Hearings?", href: "/state-hearings/" },
        { label: "Fed Drop", href: "/oig-filing-guide/" },
        { label: "HIPAA Fire", href: "/ocr-hipaa-guide/" },
        { label: "Strong Arm", href: "/right-to-request/#avmc-dossier" },
    ];

    function matchActive(href) {
        try {
            const url = new URL(href, window.location.origin);
            const currentPath = window.location.pathname.replace(/\/$/, "") || "/";
            const currentHash = window.location.hash;
            const path = url.pathname.replace(/\/$/, "") || "/";
            const hash = url.hash;

            if (hash) {
                return currentPath === path && currentHash === hash;
            }
            if (currentPath === path) {
                return true;
            }
            if (path !== "/" && currentPath.startsWith(path + '/')) {
                return true;
            }
        } catch (e) {}
        return false;
    }

    function buildDesktopLinks(links) {
        const ul = document.createElement("ul");
        ul.className = "nav-links";
        links.forEach(function (item) {
            const li = document.createElement("li");
            const a = document.createElement("a");
            a.href = item.href;
            a.textContent = item.label;
            if (matchActive(item.href)) {
                a.className = "active";
            }
            li.appendChild(a);
            ul.appendChild(li);
        });
        return ul;
    }

    function buildMobileMenu() {
        const ul = document.createElement("ul");
        ul.className = "nav-mobile-menu";
        ul.id = "nav-mobile-menu-list";

        // Row 1 first, then row 2
        LINKS_ROW1.forEach(function (item) {
            const li = document.createElement("li");
            const a = document.createElement("a");
            a.href = item.href;
            a.textContent = item.label;
            if (matchActive(item.href)) {
                a.className = "active";
            }
            li.appendChild(a);
            ul.appendChild(li);
        });

        LINKS_ROW2.forEach(function (item) {
            const li = document.createElement("li");
            const a = document.createElement("a");
            a.href = item.href;
            a.textContent = item.label;
            a.classList.add("row2");
            if (matchActive(item.href)) {
                a.classList.add("active");
            }
            li.appendChild(a);
            ul.appendChild(li);
        });

        return ul;
    }

    function buildNav() {
        const style = document.createElement("style");
        style.textContent = CSS;
        document.head.appendChild(style);

        const nav = document.createElement("nav");
        nav.id = "calaudit-nav";
        nav.setAttribute("aria-label", "Primary");

        const inner = document.createElement("div");
        inner.className = "nav-inner";

        // Logo (Centered at the top of nav-inner on desktop)
        const logo = document.createElement("a");
        logo.className = "nav-logo";
        logo.href = "/";
        logo.innerHTML = '<span class="logo-bracket">[</span>CalAudit<span class="logo-bracket">]</span>';
        inner.appendChild(logo);

        // Desktop Row 1 (Centered under the logo)
        const row1 = document.createElement("div");
        row1.className = "nav-row nav-row-1";
        row1.appendChild(buildDesktopLinks(LINKS_ROW1));
        inner.appendChild(row1);

        // Desktop Row 2 (Centered under Row 1)
        const row2 = document.createElement("div");
        row2.className = "nav-row nav-row-2";
        row2.appendChild(buildDesktopLinks(LINKS_ROW2));
        inner.appendChild(row2);

        // Hamburger button (Inside nav-inner, put before the menu in DOM order)
        const btn = document.createElement("button");
        btn.className = "nav-hamburger";
        btn.setAttribute("aria-label", "Toggle navigation");
        btn.setAttribute("aria-expanded", "false");
        btn.setAttribute("aria-controls", "nav-mobile-menu-list");
        btn.innerHTML = "&#9776;";
        inner.appendChild(btn);

        // Mobile menu â€” single merged ul (inside nav-inner)
        const mobileMenu = buildMobileMenu();
        inner.appendChild(mobileMenu);

        function closeMenu(restoreFocus) {
            if (mobileMenu.classList.contains("open")) {
                mobileMenu.classList.remove("open");
                btn.setAttribute("aria-expanded", "false");
                if (restoreFocus) btn.focus();
            }
        }

        btn.addEventListener("click", function () {
            const isOpen = mobileMenu.classList.toggle("open");
            btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
            if (isOpen) {
                const firstLink = mobileMenu.querySelector("a");
                if (firstLink) firstLink.focus();
            }
        });

        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") {
                closeMenu(true);
            }
        });

        document.addEventListener("click", function (e) {
            if (mobileMenu.classList.contains("open") && !nav.contains(e.target)) {
                closeMenu(false);
            }
        });

        nav.appendChild(inner);

        // Accessibility
        const skipLink = document.querySelector(".skip-link, .skip-to-content");
        const mainContent = document.getElementById("main-content");
        if (skipLink) {
            skipLink.parentNode.insertBefore(nav, skipLink.nextSibling);
            skipLink.addEventListener("click", function () {
                if (mainContent) mainContent.focus();
            });
        } else if (document.body) {
            document.body.insertBefore(nav, document.body.firstChild);
        }
        if (mainContent && !mainContent.hasAttribute("tabindex")) {
            mainContent.setAttribute("tabindex", "-1");
        }

    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", buildNav);
    } else {
        buildNav();
    }
})();
