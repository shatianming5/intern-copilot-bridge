(function () {
  "use strict";

  const SNAPSHOT_URL = "/api/snapshot";
  const PROJECT_ALL = "__all__";
  const PROJECT_NULL = "__null__";
  const REFRESH_MS = 30000;
  const PIXEL_ASSET_ROOT = "/monitor/static/assets/pixel-agents";
  const UTC_PLUS_8_OFFSET_MS = 8 * 60 * 60 * 1000;
  const TILE_SIZE = 16;
  const OFFICE_COLUMNS = 21;
  const OFFICE_ROWS = 22;
  const OFFICE_WIDTH = OFFICE_COLUMNS * TILE_SIZE;
  const OFFICE_HEIGHT = OFFICE_ROWS * TILE_SIZE;
  const OFFICE_VIEWPORT = {
    x: 0,
    y: 9 * TILE_SIZE,
    width: 20 * TILE_SIZE,
    height: 12 * TILE_SIZE,
  };
  const QUIET_WALNUT_PLANK_FLOOR = {
    name: "quiet-walnut-plank",
    pattern: "wide-plank",
    baseColors: ["#3f302c", "#463631", "#392c28"],
    highlight: "rgba(140, 118, 102, 0.09)",
    shadow: "rgba(20, 14, 13, 0.25)",
    seam: "rgba(17, 12, 11, 0.26)",
  };
  const SMOKED_LINEAR_WOOD_FLOOR = {
    name: "smoked-linear-grain",
    pattern: "linear-grain",
    baseColors: ["#474540", "#4d4a44", "#423f3b"],
    highlight: "rgba(171, 161, 139, 0.08)",
    shadow: "rgba(18, 18, 17, 0.22)",
    seam: "rgba(16, 15, 14, 0.18)",
  };
  // Pixel Agents default layout uses tile 7 for the left room and 1/9 for the right room.
  const WOOD_FLOOR_STYLES_BY_TILE = new Map([
    [7, SMOKED_LINEAR_WOOD_FLOOR],
    [1, QUIET_WALNUT_PLANK_FLOOR],
    [9, QUIET_WALNUT_PLANK_FLOOR],
  ]);
  const WALK_SPEED_PX_PER_SEC = 24;
  const MAX_DELTA_TIME_SEC = 0.1;
  const INITIAL_WANDER_PAUSE_MIN_MS = 2000;
  const INITIAL_WANDER_PAUSE_MAX_MS = 8000;
  const WANDER_PAUSE_MIN_MS = 8000;
  const WANDER_PAUSE_MAX_MS = 18000;
  const WANDER_MIN_DISTANCE_PX = 32;
  const WANDER_ATTEMPT_CHANCE = 0.4;

  const FURNITURE_IMAGES = {
    BIN: "furniture/BIN/BIN.png",
    BOOKSHELF: "furniture/BOOKSHELF/BOOKSHELF.png",
    CACTUS: "furniture/CACTUS/CACTUS.png",
    CLOCK: "furniture/CLOCK/CLOCK.png",
    COFFEE: "furniture/COFFEE/COFFEE.png",
    COFFEE_TABLE: "furniture/COFFEE_TABLE/COFFEE_TABLE.png",
    CUSHIONED_BENCH: "furniture/CUSHIONED_BENCH/CUSHIONED_BENCH.png",
    CUSHIONED_CHAIR_BACK: "furniture/CUSHIONED_CHAIR/CUSHIONED_CHAIR_BACK.png",
    CUSHIONED_CHAIR_FRONT: "furniture/CUSHIONED_CHAIR/CUSHIONED_CHAIR_FRONT.png",
    CUSHIONED_CHAIR_SIDE: "furniture/CUSHIONED_CHAIR/CUSHIONED_CHAIR_SIDE.png",
    DESK_FRONT: "furniture/DESK/DESK_FRONT.png",
    DESK_SIDE: "furniture/DESK/DESK_SIDE.png",
    DOUBLE_BOOKSHELF: "furniture/DOUBLE_BOOKSHELF/DOUBLE_BOOKSHELF.png",
    HANGING_PLANT: "furniture/HANGING_PLANT/HANGING_PLANT.png",
    LARGE_PAINTING: "furniture/LARGE_PAINTING/LARGE_PAINTING.png",
    LARGE_PLANT: "furniture/LARGE_PLANT/LARGE_PLANT.png",
    PC_BACK: "furniture/PC/PC_BACK.png",
    PC_FRONT_OFF: "furniture/PC/PC_FRONT_OFF.png",
    PC_FRONT_ON_1: "furniture/PC/PC_FRONT_ON_1.png",
    PC_FRONT_ON_2: "furniture/PC/PC_FRONT_ON_2.png",
    PC_FRONT_ON_3: "furniture/PC/PC_FRONT_ON_3.png",
    PC_SIDE: "furniture/PC/PC_SIDE.png",
    PLANT: "furniture/PLANT/PLANT.png",
    PLANT_2: "furniture/PLANT_2/PLANT_2.png",
    POT: "furniture/POT/POT.png",
    SMALL_PAINTING: "furniture/SMALL_PAINTING/SMALL_PAINTING.png",
    SMALL_PAINTING_2: "furniture/SMALL_PAINTING_2/SMALL_PAINTING_2.png",
    SMALL_TABLE_FRONT: "furniture/SMALL_TABLE/SMALL_TABLE_FRONT.png",
    SMALL_TABLE_SIDE: "furniture/SMALL_TABLE/SMALL_TABLE_SIDE.png",
    SOFA_BACK: "furniture/SOFA/SOFA_BACK.png",
    SOFA_FRONT: "furniture/SOFA/SOFA_FRONT.png",
    SOFA_SIDE: "furniture/SOFA/SOFA_SIDE.png",
    TABLE_FRONT: "furniture/TABLE_FRONT/TABLE_FRONT.png",
    WHITEBOARD: "furniture/WHITEBOARD/WHITEBOARD.png",
    WOODEN_BENCH: "furniture/WOODEN_BENCH/WOODEN_BENCH.png",
    WOODEN_CHAIR_BACK: "furniture/WOODEN_CHAIR/WOODEN_CHAIR_BACK.png",
    WOODEN_CHAIR_FRONT: "furniture/WOODEN_CHAIR/WOODEN_CHAIR_FRONT.png",
    WOODEN_CHAIR_SIDE: "furniture/WOODEN_CHAIR/WOODEN_CHAIR_SIDE.png",
  };

  const SEATS = [
    { x: 56, y: 234, dir: "up" },
    { x: 120, y: 234, dir: "up" },
    { x: 64, y: 285, dir: "right" },
    { x: 112, y: 285, dir: "left" },
    { x: 64, y: 318, dir: "right" },
    { x: 112, y: 318, dir: "left" },
    { x: 232, y: 238, dir: "down" },
    { x: 232, y: 286, dir: "up" },
    { x: 212, y: 262, dir: "right" },
    { x: 276, y: 262, dir: "left" },
    { x: 56, y: 256, dir: "down" },
    { x: 120, y: 256, dir: "down" },
    { x: 264, y: 324, dir: "down" },
    { x: 24, y: 320, dir: "right" },
  ];

  const state = {
    snapshot: null,
    machineQuery: "",
    selectedMachineKeys: readMachineKeySet("monitor.selectedMachines"),
    knownMachineKeys: readMachineKeySet("monitor.knownMachines"),
    projectKey: PROJECT_ALL,
    labelsVisible: false,
    showDead: readShowDead(),
    pixelAssets: null,
    officeCanvases: [],
    actorStatesByOffice: new Map(),
    animationStarted: false,
    lastAnimationTime: 0,
  };

  function readMachineKeySet(storageKey) {
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return new Set();
      return new Set(parsed.filter((key) => typeof key === "string"));
    } catch (_e) {
      return new Set();
    }
  }

  function persistMachineKeySet(storageKey, set) {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify([...set]));
    } catch (_e) {}
  }

  function persistMachineSelection() {
    persistMachineKeySet("monitor.selectedMachines", state.selectedMachineKeys);
    persistMachineKeySet("monitor.knownMachines", state.knownMachineKeys);
  }

  function readShowDead() {
    try {
      return window.localStorage.getItem("monitor.showDead") === "1";
    } catch (_e) {
      return false;
    }
  }

  function persistShowDead(value) {
    try {
      window.localStorage.setItem("monitor.showDead", value ? "1" : "0");
    } catch (_e) {}
  }

  const elements = {
    body: document.body,
    machineFilter: document.getElementById("machine-filter"),
    machineSelector: document.getElementById("machine-selector"),
    machineOptions: document.getElementById("machine-options"),
    machineSelectAll: document.getElementById("machine-select-all"),
    machineSelectionSummary: document.getElementById("machine-selection-summary"),
    projectFilter: document.getElementById("project-filter"),
    labelToggle: document.getElementById("label-toggle"),
    showDeadToggle: document.getElementById("show-dead-toggle"),
    summary: document.getElementById("summary"),
    generatedAt: document.getElementById("generated-at"),
    machineMap: document.getElementById("machine-map"),
  };

  elements.machineFilter.addEventListener("input", () => {
    state.machineQuery = elements.machineFilter.value.trim().toLowerCase();
    elements.machineSelector.open = true;
    renderMachineOptions();
    if (state.machineQuery === "") {
      render();
    }
  });

  elements.machineFilter.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      elements.machineSelector.open = false;
      elements.machineFilter.blur();
      render();
    }
  });

  elements.machineFilter.addEventListener("search", () => {
    if (elements.machineFilter.value === "") {
      render();
    }
  });

  elements.machineSelectAll.addEventListener("change", () => {
    if (!state.snapshot) {
      return;
    }
    const machines = requireMachines(state.snapshot);
    if (elements.machineSelectAll.checked) {
      machines.forEach((machine) => state.selectedMachineKeys.add(machineKey(machine)));
    } else {
      state.selectedMachineKeys.clear();
    }
    persistMachineSelection();
    render();
  });

  elements.machineOptions.addEventListener("change", (event) => {
    if (!event.target.matches("input[data-machine-key]")) {
      return;
    }
    const key = event.target.dataset.machineKey;
    if (event.target.checked) {
      state.selectedMachineKeys.add(key);
    } else {
      state.selectedMachineKeys.delete(key);
    }
    persistMachineSelection();
    render();
  });

  elements.projectFilter.addEventListener("change", () => {
    state.projectKey = elements.projectFilter.value;
    render();
  });

  elements.labelToggle.addEventListener("change", () => {
    state.labelsVisible = elements.labelToggle.checked;
    elements.body.classList.toggle("labels-visible", state.labelsVisible);
  });

  elements.showDeadToggle.checked = state.showDead;
  elements.showDeadToggle.addEventListener("change", () => {
    state.showDead = elements.showDeadToggle.checked;
    persistShowDead(state.showDead);
    render();
  });

  async function loadSnapshot() {
    const response = await fetch(SNAPSHOT_URL, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`GET ${SNAPSHOT_URL} failed with ${response.status}`);
    }
    state.snapshot = await response.json();
    syncMachineSelection();
    if (state.pixelAssets) {
      render();
    }
  }

  async function loadPixelAssets() {
    const [layout, floors, wall, furniture, furnitureCatalog, characters] = await Promise.all([
      loadJson(`${PIXEL_ASSET_ROOT}/default-layout-1.json`),
      loadImageMap(
        Array.from({ length: 9 }, (_, index) => [
          String(index),
          `${PIXEL_ASSET_ROOT}/floors/floor_${index}.png`,
        ])
      ),
      loadImage(`${PIXEL_ASSET_ROOT}/walls/wall_0.png`),
      loadImageMap(
        Object.entries(FURNITURE_IMAGES).map(([key, path]) => [
          key,
          `${PIXEL_ASSET_ROOT}/${path}`,
        ])
      ),
      loadFurnitureCatalog(),
      Promise.all(
        Array.from({ length: 6 }, (_, index) =>
          loadImage(`${PIXEL_ASSET_ROOT}/characters/char_${index}.png`)
        )
      ),
    ]);

    validatePixelLayoutAssets(layout, furniture, furnitureCatalog);
    state.pixelAssets = {
      layout,
      floors,
      wall,
      furniture,
      furnitureCatalog,
      movement: buildMovementGrid(layout, furnitureCatalog),
      characters,
    };
  }

  async function loadFurnitureCatalog() {
    const folders = [
      ...new Set(
        Object.values(FURNITURE_IMAGES).map((path) => {
          const parts = path.split("/");
          return parts[1];
        })
      ),
    ];
    const manifests = await Promise.all(
      folders.map((folder) =>
        loadJson(`${PIXEL_ASSET_ROOT}/furniture/${folder}/manifest.json`)
      )
    );
    return buildFurnitureCatalog(manifests);
  }

  function buildFurnitureCatalog(manifests) {
    const catalog = new Map();
    manifests.forEach((manifest) => registerFurnitureCatalogEntries(catalog, manifest, {}));
    return catalog;
  }

  function registerFurnitureCatalogEntries(catalog, node, inherited) {
    const context = { ...inherited };
    ["category", "backgroundTiles", "canPlaceOnSurfaces", "canPlaceOnWalls", "orientation"].forEach(
      (key) => {
        if (Object.prototype.hasOwnProperty.call(node, key)) {
          context[key] = node[key];
        }
      }
    );

    if (node.type === "asset" && node.id) {
      const footprintW = Number(node.footprintW);
      const footprintH = Number(node.footprintH);
      if (!Number.isFinite(footprintW) || !Number.isFinite(footprintH)) {
        throw new Error(`Furniture manifest ${node.id} must include footprintW and footprintH.`);
      }
      catalog.set(node.id, {
        id: node.id,
        category: context.category || "",
        backgroundTiles: Number(context.backgroundTiles || 0),
        footprintW,
        footprintH,
      });
    }

    (node.members || []).forEach((child) =>
      registerFurnitureCatalogEntries(catalog, child, context)
    );
  }

  function validatePixelLayoutAssets(layout, furniture, furnitureCatalog) {
    const missing = layout.furniture
      .map((item) => String(item.type).replace(/:left$/, ""))
      .filter((type) => !furniture.has(type));
    if (missing.length > 0) {
      throw new Error(`Missing Pixel Agents furniture assets: ${[...new Set(missing)].join(", ")}`);
    }
    const missingCatalogEntries = layout.furniture
      .map((item) => normalizeFurnitureType(item.type))
      .filter((type) => !furnitureCatalog.has(type));
    if (missingCatalogEntries.length > 0) {
      throw new Error(
        `Missing Pixel Agents furniture metadata: ${[...new Set(missingCatalogEntries)].join(", ")}`
      );
    }
  }

  async function loadJson(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error(`GET ${path} failed with ${response.status}`);
    }
    return response.json();
  }

  async function loadImageMap(entries) {
    const loaded = await Promise.all(
      entries.map(async ([key, path]) => [key, await loadImage(path)])
    );
    return new Map(loaded);
  }

  function loadImage(path) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error(`Image failed to load: ${path}`));
      image.src = path;
    });
  }

  function buildMovementGrid(layout, furnitureCatalog) {
    const blockedTiles = getBlockedTiles(layout, furnitureCatalog);
    return {
      blockedTiles,
      walkableTiles: getWalkableTiles(layout, blockedTiles).filter(tileInOfficeViewport),
    };
  }

  function getBlockedTiles(layout, furnitureCatalog) {
    const blockedTiles = new Set();
    layout.furniture.forEach((item) => {
      const entry = furnitureCatalog.get(normalizeFurnitureType(item.type));
      if (!entry) {
        throw new Error(`Missing Pixel Agents furniture metadata: ${item.type}`);
      }
      for (let rowOffset = 0; rowOffset < entry.footprintH; rowOffset += 1) {
        if (rowOffset < entry.backgroundTiles) {
          continue;
        }
        for (let columnOffset = 0; columnOffset < entry.footprintW; columnOffset += 1) {
          blockedTiles.add(tileKey(item.col + columnOffset, item.row + rowOffset));
        }
      }
    });
    return blockedTiles;
  }

  function getWalkableTiles(layout, blockedTiles) {
    const tiles = [];
    for (let row = 0; row < layout.rows; row += 1) {
      for (let column = 0; column < layout.cols; column += 1) {
        if (isWalkableTile(layout, blockedTiles, column, row)) {
          tiles.push({ col: column, row });
        }
      }
    }
    return tiles;
  }

  function isWalkableTile(layout, blockedTiles, column, row, allowedTiles) {
    if (column < 0 || row < 0 || column >= layout.cols || row >= layout.rows) {
      return false;
    }
    const tile = tileAt(layout, column, row);
    if (tile === 0 || tile === 255) {
      return false;
    }
    const key = tileKey(column, row);
    return (allowedTiles && allowedTiles.has(key)) || !blockedTiles.has(key);
  }

  function findPath(startColumn, startRow, endColumn, endRow, movement, allowedTiles) {
    const { layout } = state.pixelAssets;
    const startKey = tileKey(startColumn, startRow);
    const endKey = tileKey(endColumn, endRow);
    if (startKey === endKey) {
      return [];
    }
    if (!isWalkableTile(layout, movement.blockedTiles, endColumn, endRow, allowedTiles)) {
      return null;
    }

    const visited = new Set([startKey]);
    const parent = new Map();
    const queue = [{ col: startColumn, row: startRow }];
    const directions = [
      { dc: 0, dr: -1 },
      { dc: 1, dr: 0 },
      { dc: 0, dr: 1 },
      { dc: -1, dr: 0 },
    ];

    while (queue.length > 0) {
      const current = queue.shift();
      const currentKey = tileKey(current.col, current.row);
      if (currentKey === endKey) {
        return reconstructPath(parent, startKey, endKey);
      }

      directions.forEach((direction) => {
        const nextColumn = current.col + direction.dc;
        const nextRow = current.row + direction.dr;
        const nextKey = tileKey(nextColumn, nextRow);
        if (visited.has(nextKey)) {
          return;
        }
        if (!isWalkableTile(layout, movement.blockedTiles, nextColumn, nextRow, allowedTiles)) {
          return;
        }
        visited.add(nextKey);
        parent.set(nextKey, currentKey);
        queue.push({ col: nextColumn, row: nextRow });
      });
    }

    return null;
  }

  function reconstructPath(parent, startKey, endKey) {
    const path = [];
    let currentKey = endKey;
    while (currentKey !== startKey) {
      const [col, row] = currentKey.split(",").map(Number);
      path.unshift({ col, row });
      currentKey = parent.get(currentKey);
      if (!currentKey) {
        return null;
      }
    }
    return path;
  }

  function tileInOfficeViewport(tile) {
    const center = tileCenter(tile.col, tile.row);
    return (
      center.x >= OFFICE_VIEWPORT.x &&
      center.x <= OFFICE_VIEWPORT.x + OFFICE_VIEWPORT.width &&
      center.y >= OFFICE_VIEWPORT.y &&
      center.y <= OFFICE_VIEWPORT.y + OFFICE_VIEWPORT.height
    );
  }

  function tileCenter(column, row) {
    return {
      x: column * TILE_SIZE + TILE_SIZE / 2,
      y: row * TILE_SIZE + TILE_SIZE / 2,
    };
  }

  function pointToTile(point) {
    return {
      col: Math.floor(point.x / TILE_SIZE),
      row: Math.floor(point.y / TILE_SIZE),
    };
  }

  function tileKey(column, row) {
    return `${column},${row}`;
  }

  function normalizeFurnitureType(type) {
    return String(type).replace(/:left$/, "");
  }

  function requireMachines(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.machines)) {
      throw new Error("Snapshot response must include a machines array.");
    }
    return snapshot.machines;
  }

  function machineDisplay(machine) {
    if (machine.label && machine.ip) {
      return `${machine.label} / ${machine.ip}`;
    }
    return machine.label || machine.ip;
  }

  function displayProjectName(project) {
    return project === null ? "null project" : String(project);
  }

  function shortInternName(name) {
    return String(name || "").replace(/^intern_/, "");
  }

  function normalizeStatus(status) {
    const value = String(status || "unknown").toLowerCase();
    if (value === "active") {
      return "working";
    }
    if (["dead", "error", "failed", "offline"].includes(value)) {
      return value;
    }
    if (value === "idle" || value === "working") {
      return value;
    }
    return "unknown";
  }

  function projectKey(project) {
    return project === null ? PROJECT_NULL : String(project);
  }

  function officeKey(machine, project) {
    return `${machineKey(machine)}|${projectKey(project.project)}`;
  }

  function machineKey(machine) {
    const scheme = machine.scheme || "http";
    const host = machine.ip || machine.label || "unknown";
    const port = machine.port === undefined || machine.port === null ? "" : String(machine.port);
    return `${scheme}://${host}:${port}`;
  }

  function machineMatches(machine) {
    if (!state.machineQuery) {
      return true;
    }
    const terms = state.machineQuery
      .split(/[\s,]+/)
      .map((t) => t.trim())
      .filter(Boolean);
    if (terms.length === 0) {
      return true;
    }
    const haystack = [
      machine.ip,
      machine.label,
      machine.owner_name,
      machine.owner_mobile,
    ]
      .map((v) => String(v || "").toLowerCase())
      .join("\n");
    return terms.some((term) => haystack.includes(term));
  }

  function projectMatches(project) {
    return state.projectKey === PROJECT_ALL || projectKey(project) === state.projectKey;
  }

  function machineSelected(machine) {
    return state.selectedMachineKeys.has(machineKey(machine));
  }

  function syncMachineSelection() {
    const machines = requireMachines(state.snapshot);
    const currentKeys = new Set(machines.map(machineKey));

    [...state.selectedMachineKeys].forEach((key) => {
      if (!currentKeys.has(key)) {
        state.selectedMachineKeys.delete(key);
      }
    });

    [...state.knownMachineKeys].forEach((key) => {
      if (!currentKeys.has(key)) {
        state.knownMachineKeys.delete(key);
      }
    });

    machines.forEach((machine) => {
      const key = machineKey(machine);
      if (!state.knownMachineKeys.has(key)) {
        state.selectedMachineKeys.add(key);
        state.knownMachineKeys.add(key);
      }
    });

    persistMachineSelection();
  }

  function selectedMachines() {
    return requireMachines(state.snapshot)
      .filter(machineSelected)
      .filter(machineMatches)
      .map((machine) => {
        const projects = requireProjects(machine)
          .filter((project) => projectMatches(project.project))
          .map((project) => ({
            ...project,
            interns: requireInterns(project, machine).filter(internVisible),
          }))
          .filter((project) => project.interns.length > 0);
        return { ...machine, projects };
      })
      .filter((machine) => machine.projects.length > 0);
  }

  function internVisible(intern) {
    if (state.showDead) return true;
    return normalizeStatus(intern.status) !== "dead";
  }

  function requireProjects(machine) {
    if (!Array.isArray(machine.projects)) {
      throw new Error(`Machine ${machineDisplay(machine)} must include projects array.`);
    }
    return machine.projects;
  }

  function requireInterns(project, machine) {
    if (!Array.isArray(project.interns)) {
      throw new Error(
        `Project ${displayProjectName(project.project)} on ${machineDisplay(machine)} must include interns array.`
      );
    }
    return project.interns;
  }

  function renderMachineOptions() {
    if (!state.snapshot) {
      return;
    }

    const machines = requireMachines(state.snapshot);
    const selectedCount = machines.filter(machineSelected).length;
    const visibleMachines = machines.filter(machineMatches);

    elements.machineSelectionSummary.textContent =
      `${selectedCount}/${machines.length} machine${machines.length === 1 ? "" : "s"} selected`;
    elements.machineSelectAll.checked = machines.length > 0 && selectedCount === machines.length;
    elements.machineSelectAll.indeterminate = selectedCount > 0 && selectedCount < machines.length;
    elements.machineSelectAll.disabled = machines.length === 0;
    elements.machineOptions.replaceChildren();

    if (visibleMachines.length === 0) {
      elements.machineOptions.append(textElement("p", "No machines match this search.", "machine-choice-empty"));
      return;
    }

    visibleMachines.forEach((machine) => {
      elements.machineOptions.append(renderMachineChoice(machine));
    });
  }

  function renderMachineChoice(machine) {
    const key = machineKey(machine);
    const projects = requireProjects(machine);
    const agentCount = projects.reduce(
      (total, project) => total + requireInterns(project, machine).length,
      0
    );
    const choice = document.createElement("label");
    choice.className = "machine-choice";
    if (machine.reachable === false) {
      choice.classList.add("machine-choice-offline");
    }

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.machineKey = key;
    checkbox.checked = state.selectedMachineKeys.has(key);

    const text = elementWithClass("span", "machine-choice-text");
    text.append(textElement("span", machineDisplay(machine), "machine-choice-name"));
    text.append(
      textElement(
        "span",
        `${machine.port ? `exporter:${machine.port}` : "exporter"} · ` +
          `${projects.length}p/${agentCount}a · ` +
          `${machine.reachable === false ? "offline" : "online"}`,
        "machine-choice-meta"
      )
    );

    choice.append(checkbox, text);
    return choice;
  }

  function renderProjectOptions() {
    const previousKey = state.projectKey;
    const projects = new Map();

    requireMachines(state.snapshot)
      .filter(machineSelected)
      .filter(machineMatches)
      .forEach((machine) => {
        requireProjects(machine).forEach((project) => {
          projects.set(projectKey(project.project), displayProjectName(project.project));
        });
      });

    elements.projectFilter.replaceChildren(option("All projects", PROJECT_ALL));
    [...projects.entries()]
      .sort((left, right) => left[1].localeCompare(right[1]))
      .forEach(([value, label]) => {
        elements.projectFilter.append(option(label, value));
      });

    state.projectKey = projects.has(previousKey) ? previousKey : PROJECT_ALL;
    elements.projectFilter.value = state.projectKey;
  }

  function render() {
    if (!state.snapshot) {
      return;
    }

    renderMachineOptions();
    renderProjectOptions();
    elements.machineMap.replaceChildren();
    state.officeCanvases = [];
    const visibleOfficeKeys = new Set();
    const machines = selectedMachines();
    let projectCount = 0;
    let agentCount = 0;

    machines.forEach((machine) => {
      const region = renderMachine(machine, visibleOfficeKeys);
      elements.machineMap.append(region);
      projectCount += machine.projects.length;
      machine.projects.forEach((project) => {
        agentCount += requireInterns(project, machine).length;
      });
    });
    pruneActorStates(visibleOfficeKeys);

    if (machines.length === 0) {
      elements.machineMap.append(emptyState("No machines match the current filters."));
    }

    elements.summary.textContent =
      `${machines.length} machine${machines.length === 1 ? "" : "s"} · ` +
      `${projectCount} project${projectCount === 1 ? "" : "s"} · ` +
      `${agentCount} agent${agentCount === 1 ? "" : "s"}`;
    elements.generatedAt.textContent = state.snapshot.generated_at
      ? `Snapshot ${formatUtcPlus8Timestamp(state.snapshot.generated_at)}`
      : "";
    drawOffices(performance.now());
    startAnimationLoop();
  }

  function formatUtcPlus8Timestamp(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      throw new Error(`Invalid snapshot generated_at timestamp: ${value}`);
    }
    const shifted = new Date(date.getTime() + UTC_PLUS_8_OFFSET_MS);
    return `${shifted.toISOString().slice(0, 19).replace("T", " ")} UTC+8`;
  }

  function renderMachineMeta(machine) {
    const meta = document.createElement("div");
    meta.className = "machine-meta";

    meta.append(renderOwnerChip(machine));

    if (machine.ip) meta.append(metaChip("ip", machine.ip));

    const res = machine.resources || {};
    if (Array.isArray(res.loadavg)) {
      meta.append(metaChip("load", res.loadavg.map((v) => Number(v).toFixed(2)).join(" ")));
    }
    if (res.disk_free_gb != null) {
      const gb = Number(res.disk_free_gb);
      meta.append(metaChip("disk", `${gb.toFixed(1)}G`, gb < 20 ? "warn" : ""));
    }

    if (machine.extension_version) meta.append(metaChip("ext", machine.extension_version));
    if (machine.hooks_version && machine.hooks_version !== machine.extension_version) {
      meta.append(metaChip("hooks", machine.hooks_version));
    }
    if (machine.daemon_hash) meta.append(metaChip("daemon", machine.daemon_hash));

    const cli = machine.cli_versions || {};
    const cliParts = [];
    if (cli.claude) cliParts.push("claude " + shortVer(cli.claude));
    if (cli.codex) cliParts.push("codex " + shortVer(cli.codex));
    if (cli.python) cliParts.push("py " + shortVer(cli.python));
    if (cliParts.length) meta.append(metaChip("cli", cliParts.join(" · ")));

    if (machine.connected_at) meta.append(metaChip("conn", connectedAgo(machine.connected_at)));

    return meta;
  }

  function renderOwnerChip(machine) {
    const chip = document.createElement("span");
    chip.className = "meta-chip meta-chip-owner";
    if (machine.owner_open_id) chip.title = "open_id: " + machine.owner_open_id;

    if (machine.owner_avatar) {
      const img = document.createElement("img");
      img.className = "owner-avatar";
      img.src = machine.owner_avatar;
      img.alt = machine.owner_name || "owner";
      img.loading = "lazy";
      chip.append(img);
    }

    const key = document.createElement("span");
    key.className = "meta-key";
    key.textContent = "owner";
    chip.append(key);

    const value = document.createElement("span");
    value.className = "meta-value";
    if (machine.owner_name) {
      value.textContent = machine.owner_name;
      if (machine.owner_mobile) {
        const sub = document.createElement("span");
        sub.className = "meta-sub";
        sub.textContent = " " + machine.owner_mobile;
        value.append(sub);
      }
    } else if (machine.owner_mobile) {
      value.textContent = machine.owner_mobile;
    } else {
      value.textContent = "unknown";
      chip.classList.add("meta-chip-muted");
    }
    chip.append(value);
    return chip;
  }

  function shortVer(s) {
    if (!s) return "";
    const m = String(s).match(/\d+(?:\.\d+){1,2}/);
    return m ? m[0] : String(s).slice(0, 16);
  }

  function connectedAgo(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const sec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (sec < 60) return sec + "s";
    if (sec < 3600) return Math.floor(sec / 60) + "m";
    if (sec < 86400) return Math.floor(sec / 3600) + "h";
    return Math.floor(sec / 86400) + "d";
  }

  function metaChip(label, value, extraClass) {
    const chip = document.createElement("span");
    chip.className = `meta-chip${extraClass ? " meta-chip-" + extraClass : ""}`;
    const k = document.createElement("span");
    k.className = "meta-key";
    k.textContent = label;
    const v = document.createElement("span");
    v.className = "meta-value";
    v.textContent = String(value);
    chip.append(k, v);
    return chip;
  }

  function renderWarnings(warnings) {
    if (!Array.isArray(warnings) || warnings.length === 0) return null;
    const bar = document.createElement("div");
    bar.className = "warnings-bar";
    warnings.forEach((w) => {
      const tag = document.createElement("span");
      tag.className = "warning-tag";
      tag.textContent = w.code || "warning";
      if (w.detail) tag.title = w.detail;
      bar.append(tag);
    });
    return bar;
  }

  function renderMachine(machine, visibleOfficeKeys) {
    const region = document.createElement("article");
    region.className = "machine-region";
    if (machine.reachable === false) {
      region.classList.add("offline");
    }
    region.dataset.machineIp = machine.ip;
    region.dataset.machineLabel = machine.label || "";

    const header = document.createElement("header");
    header.className = "machine-header";

    const titleBlock = document.createElement("div");
    titleBlock.className = "machine-title";
    titleBlock.append(textElement("h2", machineDisplay(machine)));
    titleBlock.append(renderMachineMeta(machine));

    const stateLabel = document.createElement("span");
    stateLabel.className = "machine-state";
    stateLabel.append(elementWithClass("span", "state-dot"));
    stateLabel.append(document.createTextNode(machine.reachable === false ? "offline" : "online"));

    header.append(titleBlock, stateLabel);
    region.append(header);

    const warningsBar = renderWarnings(machine.warnings);
    if (warningsBar) region.append(warningsBar);

    const projectGrid = document.createElement("div");
    projectGrid.className = "project-grid";
    if (machine.projects.length === 0) {
      projectGrid.append(
        emptyState(machine.reachable === false ? "Machine unreachable; configured region is offline." : "No projects reported.")
      );
    } else {
      machine.projects.forEach((project) => {
        projectGrid.append(renderProject(machine, project, visibleOfficeKeys));
      });
    }
    region.append(projectGrid);

    return region;
  }

  function renderProject(machine, project, visibleOfficeKeys) {
    const zone = document.createElement("section");
    zone.className = "project-zone";
    zone.dataset.project = projectKey(project.project);

    zone.append(textElement("h3", displayProjectName(project.project)));

    const interns = requireInterns(project, machine);
    const key = officeKey(machine, project);
    visibleOfficeKeys.add(key);
    syncOfficeActors(key, interns);
    const office = document.createElement("div");
    office.className = "office-map";
    // tier is kept as a data attribute for future furniture-by-headcount work,
    // but today all offices render at a single uniform size.
    const tier = officeTier(interns.length);
    office.dataset.tier = tier.name;
    office.dataset.internCount = String(interns.length);

    if (interns.length === 0) {
      office.classList.add("empty-office");
      office.append(emptyState("No agents in this project."));
    } else {
      const canvas = document.createElement("canvas");
      canvas.className = "office-canvas";
      canvas.width = OFFICE_VIEWPORT.width;
      canvas.height = OFFICE_VIEWPORT.height;
      canvas.setAttribute("aria-hidden", "true");
      office.append(canvas);
      state.officeCanvases.push({ canvas, interns, officeKey: key });
      interns.forEach((intern, index) => {
        office.append(renderAgent(intern, index, key));
      });
    }

    zone.append(office);
    return zone;
  }

  // Office tier — size grows with intern count. "陈设" (furniture density) is a
  // follow-up: today we only scale the displayed canvas width so a 2-intern
  // office doesn't look as wide as an 8-intern one. Seats stay in the same
  // internal 320x192 canvas coordinates; density is naturally higher in tight
  // tiers because sprites are rendered at the same pixel scale.
  function officeTier(n) {
    if (n <= 2) return { name: "cozy", maxWidth: 320 };
    if (n <= 5) return { name: "standard", maxWidth: 480 };
    if (n <= 10) return { name: "spacious", maxWidth: 680 };
    if (n <= 18) return { name: "expanded", maxWidth: 840 };
    return { name: "open-plan", maxWidth: 960 };
  }

  function renderAgent(intern, index, key) {
    const status = normalizeStatus(intern.status);
    const name = shortInternName(intern.name);
    const actor = actorForIntern(key, intern, index);
    const agent = document.createElement("article");
    agent.className = `agent status-${status}`;
    agent.tabIndex = 0;
    agent.dataset.name = intern.name;
    agent.dataset.displayName = name;
    agent.dataset.status = status;
    agent.dataset.skin = intern.skin || "";
    actor.element = agent;
    positionAgentElement(agent, actor);
    const task = String(intern.task || "").trim();
    const typeTag = intern.type ? ` [${intern.type}]` : "";
    const ariaLabel = `${name} ${status}${task ? " · " + task : ""}${typeTag}`;
    agent.setAttribute("aria-label", ariaLabel);
    agent.title = ariaLabel;

    const light = elementWithClass("span", "agent-light");
    light.setAttribute("aria-label", `${status} status light`);

    const label = elementWithClass("span", "agent-label");
    label.title = ariaLabel;
    label.append(textElement("span", name, "agent-name"));
    label.append(textElement("span", status, "agent-status"));
    if (task) {
      label.append(textElement("span", task, "agent-task"));
    }

    agent.append(light, label);
    return agent;
  }

  function syncOfficeActors(key, interns) {
    let actors = state.actorStatesByOffice.get(key);
    if (!actors) {
      actors = new Map();
      state.actorStatesByOffice.set(key, actors);
    }

    const activeKeys = new Set();
    interns.forEach((intern, index) => {
      const keyForIntern = actorKey(intern, index);
      activeKeys.add(keyForIntern);
      let actor = actors.get(keyForIntern);
      const seat = seatForIndex(index);
      if (!actor) {
        actor = createActor(keyForIntern, seat, index);
        actors.set(keyForIntern, actor);
      }
      actor.index = index;
      actor.seat = seat;
      actor.status = normalizeStatus(intern.status);
    });

    [...actors.keys()].forEach((keyForIntern) => {
      if (!activeKeys.has(keyForIntern)) {
        actors.delete(keyForIntern);
      }
    });
  }

  function createActor(key, seat, index) {
    const tile = pointToTile(seat);
    return {
      key,
      index,
      x: seat.x,
      y: seat.y,
      tileCol: tile.col,
      tileRow: tile.row,
      seat,
      target: null,
      path: [],
      destinationKey: null,
      dir: seat.dir,
      status: "idle",
      nextMoveAt: nextWanderTime(true),
      element: null,
    };
  }

  function actorKey(intern, index) {
    return `${intern.name || "agent"}:${index}`;
  }

  function actorForIntern(key, intern, index) {
    const actors = state.actorStatesByOffice.get(key);
    if (!actors) {
      throw new Error(`Missing actor state for office ${key}`);
    }
    const actor = actors.get(actorKey(intern, index));
    if (!actor) {
      throw new Error(`Missing actor state for ${intern.name || "agent"} in ${key}`);
    }
    return actor;
  }

  function pruneActorStates(visibleOfficeKeys) {
    [...state.actorStatesByOffice.keys()].forEach((key) => {
      if (!visibleOfficeKeys.has(key)) {
        state.actorStatesByOffice.delete(key);
      }
    });
  }

  function positionAgentElement(element, actor) {
    element.style.setProperty(
      "--agent-x",
      `${((actor.x - OFFICE_VIEWPORT.x) / OFFICE_VIEWPORT.width) * 100}%`
    );
    element.style.setProperty(
      "--agent-y",
      `${((actor.y - OFFICE_VIEWPORT.y) / OFFICE_VIEWPORT.height) * 100}%`
    );
  }

  function updateActors(time, dt) {
    state.actorStatesByOffice.forEach((actors) => {
      actors.forEach((actor) => {
        updateActor(actor, time, dt);
        if (actor.element) {
          positionAgentElement(actor.element, actor);
        }
      });
    });
  }

  function updateActor(actor, time, dt) {
    if (["dead", "error", "failed", "offline"].includes(actor.status)) {
      clearActorPath(actor);
      return;
    }

    if (actor.status === "working") {
      if (distance(actor, actor.seat) > 1) {
        if (!isActorHeadingTo(actor, actor.seat)) {
          setActorDestination(actor, actor.seat, true);
        }
      } else {
        clearActorPath(actor);
        actor.dir = actor.seat.dir;
      }
    } else if (!actor.target && time >= actor.nextMoveAt) {
      if (Math.random() > WANDER_ATTEMPT_CHANCE) {
        scheduleNextWander(actor);
        return;
      }
      const target = chooseWanderTarget(actor, time);
      if (target) {
        setActorDestination(actor, target, false);
      } else {
        scheduleNextWander(actor);
      }
    }

    moveActor(actor, dt);
  }

  function setActorDestination(actor, destination, allowDestinationTile) {
    const movement = state.pixelAssets.movement;
    const startTile = { col: actor.tileCol, row: actor.tileRow };
    const endTile = pointToTile(destination);
    const allowedTiles = allowDestinationTile ? new Set([tileKey(endTile.col, endTile.row)]) : new Set();
    const path = findPath(
      startTile.col,
      startTile.row,
      endTile.col,
      endTile.row,
      movement,
      allowedTiles
    );
    if (path === null) {
      clearActorPath(actor);
      scheduleNextWander(actor);
      return false;
    }

    actor.path = path.map((tile, index) => {
      const isLast = index === path.length - 1;
      const center = tileCenter(tile.col, tile.row);
      return {
        col: tile.col,
        row: tile.row,
        x: isLast ? destination.x : center.x,
        y: isLast ? destination.y : center.y,
        dir: isLast ? destination.dir : undefined,
      };
    });
    if (actor.path.length === 0 && distance(actor, destination) > 1) {
      actor.path.push({
        col: endTile.col,
        row: endTile.row,
        x: destination.x,
        y: destination.y,
        dir: destination.dir,
      });
    }
    actor.destinationKey = destinationKey(destination);
    actor.target = null;
    advanceActorPath(actor);
    return true;
  }

  function chooseWanderTarget(actor, time) {
    const candidates = state.pixelAssets.movement.walkableTiles.filter((tile) => {
      const point = tileCenter(tile.col, tile.row);
      return distance(actor, point) > WANDER_MIN_DISTANCE_PX;
    });
    if (candidates.length === 0) {
      return null;
    }
    const offset = Math.floor(time / 1000) + actor.index * 7;
    for (let index = 0; index < candidates.length; index += 1) {
      const tile = candidates[(offset + index * 5) % candidates.length];
      const point = tileCenter(tile.col, tile.row);
      const path = findPath(
        actor.tileCol,
        actor.tileRow,
        tile.col,
        tile.row,
        state.pixelAssets.movement,
        new Set()
      );
      if (path !== null && path.length > 0) {
        return point;
      }
    }
    return null;
  }

  function moveActor(actor, dt) {
    if (!actor.target) {
      return;
    }
    const dx = actor.target.x - actor.x;
    const dy = actor.target.y - actor.y;
    const remaining = Math.hypot(dx, dy);
    const step = WALK_SPEED_PX_PER_SEC * dt;
    if (remaining <= step || remaining < 0.5) {
      actor.x = actor.target.x;
      actor.y = actor.target.y;
      actor.tileCol = actor.target.col;
      actor.tileRow = actor.target.row;
      actor.dir = actor.target.dir || actor.dir;
      advanceActorPath(actor);
      if (!actor.target) {
        scheduleNextWander(actor);
      }
      return;
    }
    actor.x += (dx / remaining) * step;
    actor.y += (dy / remaining) * step;
  }

  function advanceActorPath(actor) {
    const next = actor.path.shift();
    if (!next) {
      actor.target = null;
      actor.destinationKey = null;
      return;
    }
    actor.target = next;
    actor.dir =
      next.dir || directionBetweenTiles(actor.tileCol, actor.tileRow, next.col, next.row);
  }

  function clearActorPath(actor) {
    actor.target = null;
    actor.path = [];
    actor.destinationKey = null;
  }

  function isActorHeadingTo(actor, destination) {
    return actor.destinationKey === destinationKey(destination);
  }

  function destinationKey(destination) {
    const tile = pointToTile(destination);
    return `${tileKey(tile.col, tile.row)}:${Math.round(destination.x)},${Math.round(destination.y)}`;
  }

  function scheduleNextWander(actor) {
    actor.nextMoveAt = nextWanderTime(false);
  }

  function nextWanderTime(initial) {
    const min = initial ? INITIAL_WANDER_PAUSE_MIN_MS : WANDER_PAUSE_MIN_MS;
    const max = initial ? INITIAL_WANDER_PAUSE_MAX_MS : WANDER_PAUSE_MAX_MS;
    return performance.now() + randomBetween(min, max);
  }

  function directionBetweenTiles(fromColumn, fromRow, toColumn, toRow) {
    if (toColumn > fromColumn) {
      return "right";
    }
    if (toColumn < fromColumn) {
      return "left";
    }
    if (toRow < fromRow) {
      return "up";
    }
    if (toRow > fromRow) {
      return "down";
    }
    return "down";
  }

  function distance(from, to) {
    return Math.hypot(to.x - from.x, to.y - from.y);
  }

  function randomBetween(min, max) {
    return min + Math.random() * (max - min);
  }

  function seatForIndex(index) {
    if (index < SEATS.length) {
      return SEATS[index];
    }
    // Overflow rows: 5 columns × unlimited rows, no modulo so they don't
    // collide. Rows past ~3 will clip at the canvas edge, which is acceptable —
    // single-office overflow past ~30 interns is unrealistic for this fleet.
    const overflowIndex = index - SEATS.length;
    const column = overflowIndex % 5;
    const row = Math.floor(overflowIndex / 5);
    return {
      x: 48 + column * 56,
      y: 210 + row * 38,
      dir: "down",
    };
  }

  function startAnimationLoop() {
    if (state.animationStarted) {
      return;
    }
    state.animationStarted = true;
    window.requestAnimationFrame(function tick(time) {
      const dt = state.lastAnimationTime
        ? Math.min((time - state.lastAnimationTime) / 1000, MAX_DELTA_TIME_SEC)
        : 0;
      state.lastAnimationTime = time;
      updateActors(time, dt);
      drawOffices(time);
      window.requestAnimationFrame(tick);
    });
  }

  function drawOffices(time) {
    if (!state.pixelAssets) {
      return;
    }
    state.officeCanvases.forEach((office) =>
      drawPixelOffice(office.canvas, office.interns, time, office.officeKey)
    );
  }

  function drawPixelOffice(canvas, interns, time, officeKey) {
    const context = canvas.getContext("2d");
    const { layout } = state.pixelAssets;
    context.imageSmoothingEnabled = false;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#3b4339";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.save();
    context.translate(-OFFICE_VIEWPORT.x, -OFFICE_VIEWPORT.y);
    drawFloor(context, layout);

    const working = interns.some((intern) => normalizeStatus(intern.status) === "working");
    const drawables = wallDrawables(context, layout).concat(layout.furniture.map((item) => ({
      z: (item.row + 1) * TILE_SIZE,
      draw: () => drawFurniture(context, item, working, time),
    })));

    interns.forEach((intern, index) => {
      const actor = actorForIntern(officeKey, intern, index);
      drawables.push({
        z: actor.y + 8,
        draw: () => drawCharacter(context, intern, index, actor, time),
      });
    });

    drawables.sort((left, right) => left.z - right.z);
    drawables.forEach((item) => item.draw());
    context.restore();
  }

  function drawFloor(context, layout) {
    for (let row = 0; row < layout.rows; row += 1) {
      for (let column = 0; column < layout.cols; column += 1) {
        const tile = layout.tiles[row * layout.cols + column];
        if (tile === 255 || tile === 0) {
          continue;
        }
        const woodFloorStyle = WOOD_FLOOR_STYLES_BY_TILE.get(tile);
        if (woodFloorStyle) {
          drawWoodFloorTile(context, column, row, woodFloorStyle);
          continue;
        }
        const floor = state.pixelAssets.floors.get(String(tile)) || state.pixelAssets.floors.get("0");
        context.drawImage(floor, column * TILE_SIZE, row * TILE_SIZE);
      }
    }
  }

  function drawWoodFloorTile(context, column, row, style) {
    if (style.pattern === "wide-plank") {
      drawWidePlankWoodTile(context, column, row, style);
      return;
    }
    if (style.pattern === "linear-grain") {
      drawLinearGrainWoodTile(context, column, row, style);
      return;
    }
    throw new Error(`Unknown wood floor pattern: ${style.pattern}`);
  }

  function drawWidePlankWoodTile(context, column, row, style) {
    const x = column * TILE_SIZE;
    const y = row * TILE_SIZE;
    const plankRow = Math.floor(row / 2);
    const plankShade = (plankRow + Math.floor(column / 5)) % style.baseColors.length;

    context.fillStyle = style.baseColors[plankShade];
    context.fillRect(x, y, TILE_SIZE, TILE_SIZE);

    if (row % 2 === 1) {
      context.fillStyle = style.seam;
      context.fillRect(x, y + TILE_SIZE - 1, TILE_SIZE, 1);
    }
    if ((column + plankRow) % 4 === 0) {
      context.fillStyle = style.seam;
      context.fillRect(x, y + 1, 1, TILE_SIZE - 2);
    }
    if ((column + row) % 3 === 0) {
      context.fillStyle = style.highlight;
      context.fillRect(x + 1, y + 5, TILE_SIZE - 2, 1);
    }
    if ((column + row) % 4 === 0) {
      context.fillStyle = style.shadow;
      context.fillRect(x + 2, y + 12, TILE_SIZE - 4, 1);
    }
  }

  function drawLinearGrainWoodTile(context, column, row, style) {
    const x = column * TILE_SIZE;
    const y = row * TILE_SIZE;
    const plankShade = (Math.floor(row / 2) + Math.floor(column / 6)) % style.baseColors.length;

    context.fillStyle = style.baseColors[plankShade];
    context.fillRect(x, y, TILE_SIZE, TILE_SIZE);
    context.fillStyle = style.highlight;
    context.fillRect(x, y + 4, TILE_SIZE, 1);
    context.fillStyle = style.shadow;
    context.fillRect(x, y + 11, TILE_SIZE, 1);
    if (row % 2 === 1) {
      context.fillStyle = style.seam;
      context.fillRect(x, y + TILE_SIZE - 1, TILE_SIZE, 1);
    }
  }

  function wallDrawables(context, layout) {
    const drawables = [];
    for (let row = 0; row < layout.rows; row += 1) {
      for (let column = 0; column < layout.cols; column += 1) {
        if (layout.tiles[row * layout.cols + column] !== 0) {
          continue;
        }
        drawables.push({
          z: (row + 1) * TILE_SIZE,
          draw: () => drawWall(context, layout, column, row),
        });
      }
    }
    return drawables;
  }

  function drawWall(context, layout, column, row) {
    const mask = wallMask(layout, column, row);
    const sx = (mask % 4) * TILE_SIZE;
    const sy = Math.floor(mask / 4) * 32;
    const x = column * TILE_SIZE;
    const y = row * TILE_SIZE - TILE_SIZE;
    context.drawImage(state.pixelAssets.wall, sx, sy, TILE_SIZE, 32, x, y, TILE_SIZE, 32);
  }

  function wallMask(layout, column, row) {
    let mask = 0;
    if (tileAt(layout, column, row - 1) === 0) mask |= 1;
    if (tileAt(layout, column + 1, row) === 0) mask |= 2;
    if (tileAt(layout, column, row + 1) === 0) mask |= 4;
    if (tileAt(layout, column - 1, row) === 0) mask |= 8;
    return mask;
  }

  function tileAt(layout, column, row) {
    if (column < 0 || row < 0 || column >= layout.cols || row >= layout.rows) {
      return 255;
    }
    return layout.tiles[row * layout.cols + column];
  }

  function drawFurniture(context, item, working, time) {
    const rawType = String(item.type);
    const mirrored = rawType.endsWith(":left");
    let type = normalizeFurnitureType(rawType);
    if (type === "PC_FRONT_OFF" && working) {
      type = `PC_FRONT_ON_${(Math.floor(time / 260) % 3) + 1}`;
    }
    const image = state.pixelAssets.furniture.get(type);
    if (!image) {
      throw new Error(`Missing Pixel Agents furniture asset: ${type}`);
    }
    const x = item.col * TILE_SIZE;
    const y = item.row * TILE_SIZE;
    drawImage(context, image, x, y, image.width, image.height, mirrored);
  }

  function drawCharacter(context, intern, index, actor, time) {
    const status = normalizeStatus(intern.status);
    const character = state.pixelAssets.characters[index % state.pixelAssets.characters.length];
    const moving = Boolean(actor.target);
    const frame = characterFrame(status, index, time, moving);
    const row = directionRow(actor.dir);
    const sx = frame * TILE_SIZE;
    const sy = row * 32;
    const x = Math.round(actor.x - TILE_SIZE / 2);
    const y = Math.round(actor.y - 32);
    const mirrored = actor.dir === "left";

    context.save();
    if (["dead", "error", "failed", "offline"].includes(status)) {
      context.globalAlpha = 0.58;
    }
    drawSprite(context, character, sx, sy, TILE_SIZE, 32, x, y, mirrored);
    context.restore();
  }

  function characterFrame(status, index, time, moving) {
    if (moving) {
      return (Math.floor(time / 150) + index) % 4;
    }
    if (status === "working") {
      return 3 + ((Math.floor(time / 320) + index) % 2);
    }
    if (["dead", "error", "failed", "offline"].includes(status)) {
      return 0;
    }
    return 1;
  }

  function directionRow(direction) {
    if (direction === "up") {
      return 1;
    }
    if (direction === "left" || direction === "right") {
      return 2;
    }
    return 0;
  }

  function drawSprite(context, image, sx, sy, sw, sh, x, y, mirrored) {
    if (!mirrored) {
      context.drawImage(image, sx, sy, sw, sh, x, y, sw, sh);
      return;
    }
    context.save();
    context.translate(x + sw, y);
    context.scale(-1, 1);
    context.drawImage(image, sx, sy, sw, sh, 0, 0, sw, sh);
    context.restore();
  }

  function drawImage(context, image, x, y, width, height, mirrored) {
    if (!mirrored) {
      context.drawImage(image, x, y, width, height);
      return;
    }
    context.save();
    context.translate(x + width, y);
    context.scale(-1, 1);
    context.drawImage(image, 0, 0, width, height);
    context.restore();
  }

  function option(label, value) {
    const optionElement = document.createElement("option");
    optionElement.value = value;
    optionElement.textContent = label;
    return optionElement;
  }

  function textElement(tagName, text, className) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    element.textContent = text;
    return element;
  }

  function elementWithClass(tagName, className) {
    const element = document.createElement(tagName);
    element.className = className;
    return element;
  }

  function emptyState(message) {
    return textElement("p", message, "empty-state");
  }

  loadPixelAssets()
    .then(() => loadSnapshot())
    .catch((error) => {
      elements.summary.textContent = "Snapshot unavailable";
      elements.generatedAt.textContent = "";
      elements.machineMap.replaceChildren(textElement("p", error.message, "load-error"));
    });

  window.setInterval(() => {
    loadSnapshot().catch((error) => {
      elements.summary.textContent = "Snapshot refresh failed";
      elements.generatedAt.textContent = error.message;
    });
  }, REFRESH_MS);
})();
