const paletteLabels = {
  bg: "Фон", surface: "Карточки", "surface-raised": "Выделенные карточки",
  input: "Поля ввода", text: "Текст", muted: "Вторичный текст",
  primary: "Основные кнопки", "on-primary": "Текст на кнопках",
  border: "Границы", "border-strong": "Активные границы", subtle: "Приглушённый текст",
  overlay: "Затемнение", glow: "Свечение", grid: "Сетка", focus: "Фокус",
  success: "Успешные действия", danger: "Важные предупреждения",
};
const paletteAlpha = {overlay: "eb", glow: "1f", grid: "0d"};
const paletteForceHex = {overlay: "#090909", glow: "#ffffff", grid: "#ffffff"};
const paletteForm = document.getElementById("brand-palette-form");
const palettePreview = document.getElementById("brand-preview");
const paletteFields = new Map();
let paletteDraft = {};

const defaultRule = document.getElementById("theme-base").sheet?.cssRules[0]?.style;
for (const [key, title] of Object.entries(paletteLabels)) {
  const field = document.createElement("div");
  field.className = "palette-field";
  const label = document.createElement("label");
  const labelId = `palette-label-${key}`;
  label.id = labelId;
  label.htmlFor = `palette-hex-${key}`;
  label.textContent = title;
  const color = document.createElement("input");
  color.type = "color";
  color.setAttribute("aria-labelledby", labelId);
  const hex = document.createElement("input");
  hex.type = "text";
  hex.id = label.htmlFor;
  hex.className = "palette-hex";
  hex.pattern = "#[0-9a-fA-F]{6}";
  hex.maxLength = 7;
  hex.spellcheck = false;
  hex.autocomplete = "off";
  hex.required = true;
  const base = paletteForceHex[key] || defaultRule?.getPropertyValue("--color-" + key).trim() || "#000000";
  const fallback = base.length === 4 ? "#" + [...base.slice(1)].map(char => char + char).join("") : base;
  function update(value) {
    if (!/^#[a-f0-9]{6}$/i.test(value)) return;
    color.value = value;
    if (value.toLowerCase() === fallback) delete paletteDraft[key];
    else paletteDraft[key] = value.toLowerCase();
    paintPalette();
  }
  color.oninput = () => { hex.value = color.value.toUpperCase(); update(color.value); };
  hex.oninput = () => update(hex.value);
  field.append(label, color, hex);
  const advanced = Object.keys(paletteLabels).indexOf(key) >= 8;
  document.getElementById(advanced ? "brand-palette-advanced" : "brand-palette").append(field);
  paletteFields.set(key, {color, hex, fallback});
}

function paintPalette() {
  for (const [key, field] of paletteFields) {
    palettePreview.style.setProperty("--color-" + key, (paletteDraft[key] || field.fallback) + (paletteAlpha[key] || ""));
  }
}

function fillPalette(colors) {
  paletteDraft = {...colors};
  for (const [key, {color, hex, fallback}] of paletteFields) {
    color.value = colors[key] || fallback;
    hex.value = color.value.toUpperCase();
  }
  paintPalette();
}

function setupPalette(save) {
  const reset = document.getElementById("brand-palette-reset");
  const status = document.getElementById("brand-palette-status");
  async function submit(colors, button) {
    const buttons = [button, reset, ...paletteForm.querySelectorAll("input, button[type='submit']")];
    buttons.forEach(field => { field.disabled = true; });
    status.textContent = "Сохраняем…";
    try {
      const result = await save(colors);
      fillPalette(result.colors);
      const stylesheet = document.getElementById("store-theme");
      stylesheet.href = "/store-theme.css?v=" + Date.now();
      status.textContent = "Палитра применена.";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      buttons.forEach(field => { field.disabled = false; });
    }
  }
  const advanced = paletteForm.querySelector("details");
  advanced.addEventListener("invalid", () => { advanced.open = true; }, true);
  fillPalette({});
  paletteForm.onsubmit = event => {
    event.preventDefault();
    submit({...paletteDraft}, event.submitter);
  };
  reset.onclick = () => {
    if (window.confirm("Вернуть стандартные цвета Remnaum для всего магазина?")) submit({}, reset);
  };
}
