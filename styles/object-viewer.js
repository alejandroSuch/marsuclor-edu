<script>
(() => {
  const presets = {
    iso: { yaw: Math.PI / 4, elevation: 0.58, label: "Perspectiva del dibuix" },
    front: { yaw: 0, elevation: 0, label: "Alçat: mirada des del davant" },
    top: { yaw: 0, elevation: Math.PI / 2, label: "Planta: mirada des de dalt" },
    right: { yaw: Math.PI / 2, elevation: 0, label: "Perfil dret: mirada des de la dreta" },
  };

  function vector(x, y, z) { return [x, y, z]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function cross(a, b) {
    return vector(a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]);
  }
  function normalize(a) {
    const length = Math.hypot(...a);
    return a.map(value => value / length);
  }
  function edgeKey(a, b) {
    const points = [a.join(","), b.join(",")].sort();
    return points.join("|");
  }

  function makeFaces(heights) {
    const rows = heights.length;
    const columns = heights[0].length;
    const faces = [];
    const edges = new Map();
    const heightAt = (x, z) => heights[z]?.[x] ?? 0;
    const add = (vertices, normal, color) => {
      const face = { vertices, normal, color, edges: [] };
      for (let i = 0; i < 4; i++) {
        const key = edgeKey(vertices[i], vertices[(i + 1) % 4]);
        face.edges.push(key);
        if (!edges.has(key)) edges.set(key, new Set());
        edges.get(key).add(normal.join(","));
      }
      faces.push(face);
    };
    for (let z = 0; z < rows; z++) {
      for (let x = 0; x < columns; x++) {
        const height = heights[z][x];
        if (!height) continue;
        add([[x, height, z], [x + 1, height, z], [x + 1, height, z + 1], [x, height, z + 1]], vector(0, 1, 0), "#f1f5f4");
        for (let y = 0; y < height; y++) {
          if (heightAt(x, z - 1) <= y)
            add([[x, y, z], [x + 1, y, z], [x + 1, y + 1, z], [x, y + 1, z]], vector(0, 0, -1), "#8da4b0");
          if (heightAt(x, z + 1) <= y)
            add([[x, y, z + 1], [x + 1, y, z + 1], [x + 1, y + 1, z + 1], [x, y + 1, z + 1]], vector(0, 0, 1), "#adbec5");
          if (heightAt(x - 1, z) <= y)
            add([[x, y, z], [x, y, z + 1], [x, y + 1, z + 1], [x, y + 1, z]], vector(-1, 0, 0), "#a8bac2");
          if (heightAt(x + 1, z) <= y)
            add([[x + 1, y, z], [x + 1, y, z + 1], [x + 1, y + 1, z + 1], [x + 1, y + 1, z]], vector(1, 0, 0), "#c2d2d9");
        }
      }
    }
    // Edges shared by coplanar faces have one normal; only exterior edges are drawn.
    const counts = new Map();
    for (const face of faces) for (const key of face.edges) counts.set(key, (counts.get(key) || 0) + 1);
    for (const face of faces) face.drawEdges = face.edges.map(key => counts.get(key) === 1 || edges.get(key).size > 1);
    return faces;
  }

  function mount(root) {
    const heights = JSON.parse(root.dataset.heights);
    const canvas = root.querySelector("canvas");
    const context = canvas.getContext("2d");
    if (!context) return;
    const faces = makeFaces(heights);
    const columns = heights[0].length;
    const rows = heights.length;
    const maxHeight = Math.max(...heights.flat());
    const status = root.querySelector(".viewer-status");
    const buttons = [...root.querySelectorAll("[data-view]")];
    let yaw = presets.iso.yaw;
    let elevation = presets.iso.elevation;
    let dragging = null;

    function render() {
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (!width || !height) return;
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      const pixelWidth = Math.round(width * pixelRatio);
      const pixelHeight = Math.round(height * pixelRatio);
      if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
      }
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      context.clearRect(0, 0, width, height);
      const direction = vector(Math.sin(yaw) * Math.cos(elevation), Math.sin(elevation), -Math.cos(yaw) * Math.cos(elevation));
      const right = Math.abs(direction[1]) > 0.99999 ? vector(1, 0, 0) : normalize(cross(direction, vector(0, 1, 0)));
      const up = normalize(cross(right, direction));
      const center = vector(columns / 2, maxHeight / 2, rows / 2);
      const projected = point => {
        const relative = point.map((value, index) => value - center[index]);
        return [dot(relative, right), dot(relative, up)];
      };
      const corners = [];
      for (const x of [0, columns]) for (const y of [0, maxHeight]) for (const z of [0, rows]) corners.push(projected([x, y, z]));
      const xs = corners.map(point => point[0]);
      const ys = corners.map(point => point[1]);
      const scale = Math.min((width - 44) / (Math.max(...xs) - Math.min(...xs)), (height - 34) / (Math.max(...ys) - Math.min(...ys)));
      const screen = point => {
        const [x, y] = projected(point);
        return [width / 2 + x * scale, height / 2 - y * scale];
      };
      const visible = faces.filter(face => dot(face.normal, direction) > 0.00001);
      visible.sort((a, b) => {
        const depth = face => face.vertices.reduce((total, point) => total + dot(point, direction), 0) / 4;
        return depth(a) - depth(b);
      });
      context.lineJoin = "round";
      context.lineCap = "round";
      context.lineWidth = Math.max(1.3, Math.min(2.1, scale * 0.035));
      for (const face of visible) {
        const points = face.vertices.map(screen);
        context.beginPath();
        context.moveTo(...points[0]);
        for (let i = 1; i < 4; i++) context.lineTo(...points[i]);
        context.closePath();
        context.fillStyle = face.color;
        context.fill();
        context.strokeStyle = face.color;
        context.lineWidth = 1;
        context.stroke();
        context.strokeStyle = "#243744";
        context.lineWidth = Math.max(1.3, Math.min(2.1, scale * 0.035));
        for (let i = 0; i < 4; i++) {
          if (!face.drawEdges[i]) continue;
          context.beginPath();
          context.moveTo(...points[i]);
          context.lineTo(...points[(i + 1) % 4]);
          context.stroke();
        }
      }
    }

    function select(view) {
      yaw = presets[view].yaw;
      elevation = presets[view].elevation;
      buttons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
      status.textContent = presets[view].label;
      render();
    }
    buttons.forEach(button => button.addEventListener("click", () => select(button.dataset.view)));
    canvas.addEventListener("pointerdown", event => {
      dragging = { id: event.pointerId, x: event.clientX, y: event.clientY };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", event => {
      if (!dragging || dragging.id !== event.pointerId) return;
      yaw += (event.clientX - dragging.x) * 0.012;
      elevation = Math.max(0.08, Math.min(1.5, elevation - (event.clientY - dragging.y) * 0.012));
      dragging.x = event.clientX;
      dragging.y = event.clientY;
      buttons.forEach(button => button.setAttribute("aria-pressed", "false"));
      render();
    });
    canvas.addEventListener("pointerup", () => { dragging = null; status.textContent = "Peça girada. Prem Com en l’exercici per tornar al dibuix inicial."; });
    canvas.addEventListener("pointercancel", () => { dragging = null; });
    canvas.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      yaw += (event.key === "ArrowLeft" ? -0.12 : event.key === "ArrowRight" ? 0.12 : 0);
      elevation = Math.max(0.08, Math.min(1.5, elevation + (event.key === "ArrowUp" ? 0.12 : event.key === "ArrowDown" ? -0.12 : 0)));
      buttons.forEach(button => button.setAttribute("aria-pressed", "false"));
      status.textContent = "Peça girada. Prem Com en l’exercici per tornar al dibuix inicial.";
      render();
    });
    new ResizeObserver(render).observe(canvas);
    render();
  }

  document.querySelectorAll(".object-viewer").forEach(mount);
})();
</script>
