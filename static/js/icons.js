(function (global) {
  "use strict";

  const DEFAULT_SIZE = 16;

  function toPascalIconName(name) {
    if (!name) return "";
    return name
      .split("-")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
      .join("");
  }

  function toKebabIconName(name) {
    if (!name) return "";
    if (name.includes("-")) return name.toLowerCase();
    return name.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
  }

  function resolveIconNode(name) {
    const lib = global.lucide;
    if (!lib) return null;

    const icons = lib.icons || lib;
    const candidates = [name, toPascalIconName(name), toKebabIconName(name)].filter(Boolean);

    for (const candidate of candidates) {
      const pascal = toPascalIconName(candidate);
      if (icons[pascal]) return icons[pascal];
      if (lib[pascal]) return lib[pascal];
    }

    return null;
  }

  function lucideIcon(name, options) {
    options = options || {};
    const iconNode = resolveIconNode(name);
    const lib = global.lucide;

    if (!iconNode || !lib || typeof lib.createElement !== "function") {
      const fallback = document.createElement("span");
      fallback.className = options.className || "icon-missing";
      fallback.setAttribute("aria-hidden", "true");
      return fallback;
    }

    const size = options.size || DEFAULT_SIZE;
    const classNames = ["lucide-icon"];
    if (options.className) {
      classNames.push(...options.className.split(/\s+/).filter(Boolean));
    }

    const attrs = {
      width: size,
      height: size,
      "stroke-width": options.strokeWidth || 2,
      "aria-hidden": "true",
      class: classNames.join(" "),
    };

    if (options.fill) {
      attrs.fill = options.fill;
    }

    return lib.createElement(iconNode, attrs);
  }

  function mountIcon(container, name, options) {
    if (!container) return null;
    container.replaceChildren();
    const icon = lucideIcon(name, options);
    container.appendChild(icon);
    return icon;
  }

  function lucideIconHtml(name, options) {
    return lucideIcon(name, options).outerHTML;
  }

  function stepIconName(step) {
    if (step.type === "evaluation") return step.relevant ? "Check" : "X";
    return STEP_ICON_MAP[step.type] || "Circle";
  }

  const STEP_ICON_MAP = {
    history: "History",
    rewrite: "Pencil",
    orchestrate: "Compass",
    retrieval: "BookOpen",
    web_search: "Globe",
    generate: "MessageCircle",
    refine: "RefreshCw",
    fallback: "AlertTriangle",
    file: "FileText",
    agent_plan: "Bot",
    subagent_start: "Play",
    subagent_memory: "Brain",
    agent_aggregate: "Puzzle",
  };

  function iconSizeForElement(el) {
    if (
      el.classList.contains("login-mark") ||
      el.classList.contains("sidebar-mark")
    ) {
      return 18;
    }
    if (
      el.classList.contains("empty-icon") ||
      el.classList.contains("trace-sidebar-icon") ||
      el.classList.contains("trace-empty-icon") ||
      el.classList.contains("modal-icon")
    ) {
      return 18;
    }
    if (el.id === "plus-btn" || el.id === "send-btn") return 18;
    if (el.classList.contains("new-btn")) return 14;
    if (el.classList.contains("plus-menu-icon")) return 16;
    if (el.classList.contains("plus-menu-check")) return 14;
    if (el.classList.contains("project-chevron")) return 12;
    if (el.classList.contains("conv-icon") || el.classList.contains("project-icon")) return 14;
    return DEFAULT_SIZE;
  }

  function mountBrandMark(el) {
    return mountIcon(el, "Heart", {
      size: iconSizeForElement(el),
      fill: "currentColor",
      strokeWidth: 2,
    });
  }

  function mountFromDataAttr(root) {
    (root || document).querySelectorAll("[data-icon]").forEach((el) => {
      const raw = el.getAttribute("data-icon");
      if (el.classList.contains("login-mark") || el.classList.contains("sidebar-mark")) {
        mountBrandMark(el);
        return;
      }
      mountIcon(el, raw, { size: iconSizeForElement(el) });
    });
  }

  global.AutiaIcons = {
    lucideIcon,
    mountIcon,
    lucideIconHtml,
    stepIconName,
    mountFromDataAttr,
    mountBrandMark,
    toPascalIconName,
    toKebabIconName,
    STEP_ICON_MAP,
  };
})(window);
