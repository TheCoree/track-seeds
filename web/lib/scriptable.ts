/**
 * Виджет для iPhone и iPad (приложение Scriptable): состояние дробилок, кадры камер и итоги дня.
 * Ключ — только чтение /api/widget, отзывается в «Мой аккаунт». Размеры: малый, средний, большой,
 * очень большой (iPad), на экран блокировки — круглый, прямоугольный и строкой.
 *
 * Вид — как у системных виджетов iOS: системные цвета и шрифт SF Rounded, состояние — значок
 * SF Symbols в цветном кружке. Размер виджета берётся из таблицы Apple по экрану устройства, и все
 * фото и плитки получают точные размеры — ничего не вылезает за край ни на одном iPhone и iPad.
 * В скрипте нет обратных кавычек и «${»: он сам лежит внутри шаблонной строки.
 */
export function scriptableWidget(server: string, token: string): string {
  return `// track-seeds — дробилки на экране iPhone и iPad (Scriptable)
// Ключ только для чтения состояния; отозвать — на сайте: «Мой аккаунт» → «Виджет».
const SERVER = ${JSON.stringify(server)};
const TOKEN = ${JSON.stringify(token)};

const family = config.widgetFamily || (Device.isPad() ? "extraLarge" : "large");

// Системные цвета iOS (светлая и тёмная тема)
const dyn = (light, dark) => Color.dynamic(new Color(light), new Color(dark));
const C = {
  bg: dyn("#FFFFFF", "#1C1C1E"),
  card: dyn("#F2F2F7", "#2C2C2E"),
  track: dyn("#E5E5EA", "#3A3A3C"),
  label: dyn("#000000", "#FFFFFF"),
  secondary: Color.dynamic(new Color("#3C3C43", 0.6), new Color("#EBEBF5", 0.6)),
  green: dyn("#34C759", "#30D158"),
  orange: dyn("#FF9500", "#FF9F0A"),
  blue: dyn("#007AFF", "#0A84FF"),
  gray: dyn("#8E8E93", "#8E8E93"),
};
// Состояние дробилки → цвет и значок (SF Symbols)
const STATES = {
  running: { color: C.green, symbol: "gearshape.2.fill" },
  idle: { color: C.orange, symbol: "exclamationmark.triangle.fill" },
  stopped: { color: C.blue, symbol: "pause.fill" },
  offline: { color: C.gray, symbol: "wifi.slash" },
  unknown: { color: C.gray, symbol: "questionmark" },
};
const look = (c) => STATES[c.state] || { color: C.gray, symbol: "minus" };

const rounded = (size, weight) => {
  const fonts = { bold: Font.boldRoundedSystemFont, semibold: Font.semiboldRoundedSystemFont,
                  medium: Font.mediumRoundedSystemFont };
  return (fonts[weight] || Font.regularRoundedSystemFont)(size);
};

// Размер виджета в точках — по таблице Apple (Human Interface Guidelines → Widgets).
// Экран → [малый, средний (ширина), большой (высота), очень большой (ширина)]; высота среднего = малый.
const SIZES = {
  "430x932": [170, 364, 382], "428x926": [170, 364, 382], "414x896": [169, 360, 379],
  "414x736": [159, 348, 357], "393x852": [158, 338, 354], "390x844": [158, 338, 354],
  "375x812": [155, 329, 345], "360x780": [155, 329, 345], "375x667": [148, 321, 324],
  "320x568": [141, 292, 311], "440x956": [170, 364, 382], "402x874": [158, 338, 354],
  "1024x1366": [170, 379, 379, 800], "1032x1376": [170, 379, 379, 800], "834x1194": [155, 342, 342, 715],
  "820x1180": [155, 342, 342, 715], "834x1210": [155, 342, 342, 715], "834x1112": [150, 328, 328, 696],
  "810x1080": [146, 320, 320, 682], "768x1024": [141, 306, 306, 642], "744x1133": [141, 305, 305, 634],
};
function widgetSize() {
  const s = Device.screenSize();
  const key = Math.round(Math.min(s.width, s.height)) + "x" + Math.round(Math.max(s.width, s.height));
  const short = Math.min(s.width, s.height);
  const [small, medium, large, extra] = SIZES[key] || [Math.round(short * 0.4), Math.round(short * 0.86),
    Math.round(short * 0.9), Math.round(short * 0.86 * 2.1)];
  return {
    small: new Size(small, small), medium: new Size(medium, small), large: new Size(medium, large),
    extraLarge: new Size(extra || medium * 2 + 20, large),
  }[family] || new Size(medium, small);
}

function hours(seconds) {
  const m = Math.round((seconds || 0) / 60);
  if (m < 1) return "меньше минуты";
  return m >= 60 ? Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "") : m + " мин";
}
function sinceText(c, now) {
  if (!c.since) return "";
  return now - c.since < 60 ? "только что" : hours(now - c.since);
}
function plural(n, one, few, many) {
  const d = n % 10, h = n % 100;
  return n + " " + (d === 1 && h !== 11 ? one : d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many);
}
const shortName = (c) => c.name.replace("Дробилка ", "Д");

function request(path) {
  const req = new Request(SERVER + path);
  req.headers = { Authorization: "Bearer " + TOKEN };
  req.timeoutInterval = 15;
  return req;
}
async function load() {
  const req = request("/api/widget");
  const data = await req.loadJSON();
  if (req.response.statusCode !== 200) throw new Error(data.detail || "ошибка " + req.response.statusCode);
  return data;
}
async function photo(c, width) {
  try {
    return await request("/api/widget/" + c.id + ".jpg?w=" + Math.min(960, Math.round(width * 2))).loadImage();
  } catch (e) {
    return null;
  }
}

function text(stack, value, font, color, lines) {
  const t = stack.addText(value);
  t.font = font;
  t.textColor = color || C.label;
  t.lineLimit = lines || 1;
  t.minimumScaleFactor = 0.75;
  return t;
}
function symbol(stack, name, size, color) {
  const s = SFSymbol.named(name) || SFSymbol.named("circle.fill");
  s.applyFont(Font.boldSystemFont(size));
  const img = stack.addImage(s.image);
  img.imageSize = new Size(size, size);
  img.tintColor = color;
  return img;
}
// Значок состояния в цветном кружке — как плитки в приложении «Дом»
function badge(stack, c, size) {
  const b = stack.addStack();
  b.size = new Size(size, size);
  b.cornerRadius = size / 2;
  b.backgroundColor = look(c).color;
  b.centerAlignContent();
  symbol(b, look(c).symbol, Math.round(size * 0.5), Color.white());
  return b;
}
function fixed(stack, width, height) {
  const s = stack.addStack();
  s.size = new Size(Math.max(0, Math.round(width)), Math.max(0, Math.round(height)));
  return s;
}

// Полоса суток, как «Экранное время»: работала, без зерна, стояла, нет связи; дальше — ещё не наступило
function dayBar(stack, today, width, height) {
  const bar = fixed(stack, width, height);
  bar.cornerRadius = height / 2;
  bar.backgroundColor = C.track;
  if (!today) return;
  const parts = [[today.running, C.green], [today.idle, C.orange], [today.stopped, C.blue],
                 [(today.offline || 0) + (today.unknown || 0), C.gray]];
  let used = 0;
  for (const [seconds, color] of parts) {
    const w = Math.floor(width * (seconds || 0) / 86400);
    if (w < 1) continue;
    fixed(bar, w, height).backgroundColor = color;
    used += w;
  }
  bar.addSpacer(Math.max(0, width - used));
}
const worked = (today) => (today ? (today.running || 0) + (today.idle || 0) : 0);
function restText(today) {
  if (!today) return "";
  const parts = [];
  if (today.idle >= 60) parts.push("без зерна " + hours(today.idle));
  if (today.stopped >= 60) parts.push("стояла " + hours(today.stopped));
  parts.push(today.stops ? plural(today.stops, "остановка", "остановки", "остановок") : "без остановок");
  return parts.join(" · ");
}
// короткая длительность для экрана блокировки: «2 ч», «15 мин»
function brief(c, now) {
  if (!c.since) return "";
  const m = Math.round((now - c.since) / 60);
  return m >= 60 ? Math.floor(m / 60) + " ч" : Math.max(m, 1) + " мин";
}

// Строка «● Работает · 2 ч» — подпись состояния цветом состояния
function statusLine(stack, c, now, size) {
  const row = stack.addStack();
  row.centerAlignContent();
  text(row, c.label, rounded(size, "semibold"), look(c).color);
  const since = sinceText(c, now);
  if (since) {
    row.addSpacer(4);
    text(row, "· " + since, rounded(size, "medium"), C.secondary);
  }
  return row;
}

function header(w, now, compact) {
  const head = w.addStack();
  head.centerAlignContent();
  symbol(head, "gearshape.2.fill", compact ? 11 : 12, C.secondary);
  head.addSpacer(4);
  text(head, "ДРОБИЛКИ", rounded(compact ? 11 : 12, "semibold"), C.secondary);
  head.addSpacer();
  const clock = new DateFormatter();
  clock.dateFormat = "HH:mm";
  text(head, clock.string(new Date(now * 1000)), rounded(compact ? 11 : 12, "medium"), C.secondary);
}

// Плитка с кадром камеры: фото заполняет плитку (обрезается по краям, не растягивается),
// снизу — затемнение и подпись, как в «Фото» и «Доме»
function photoTile(stack, c, image, width, height, now) {
  const tile = fixed(stack, width, height);
  tile.cornerRadius = 14;
  tile.url = SERVER + "/crusher?id=" + encodeURIComponent(c.id);
  if (!image) {
    tile.backgroundColor = C.card;
    tile.layoutVertically();
    tile.setPadding(10, 10, 10, 10);
    const top = tile.addStack();
    badge(top, c, 26);
    tile.addSpacer();
    text(tile, c.name, rounded(15, "semibold"));
    statusLine(tile, c, now, 12);
    return tile;
  }
  tile.backgroundImage = image;
  const shade = fixed(tile, width, height);  // затемнение снизу — подпись читается на любом кадре
  const gradient = new LinearGradient();
  gradient.colors = [new Color("#000000", 0), new Color("#000000", 0.62)];
  gradient.locations = [0.42, 1];
  shade.backgroundGradient = gradient;
  shade.layoutVertically();
  shade.setPadding(8, 9, 8, 9);
  badge(shade, c, 24);
  shade.addSpacer();
  text(shade, c.name, rounded(14, "semibold"), Color.white());
  const since = sinceText(c, now);
  text(shade, c.label + (since ? " · " + since : ""), rounded(11, "semibold"), new Color("#FFFFFF", 0.88));
  return tile;
}

function errorWidget(w, message) {
  const head = w.addStack();
  head.centerAlignContent();
  symbol(head, "wifi.exclamationmark", 16, C.orange);
  head.addSpacer(6);
  text(head, "Дробилки", rounded(15, "semibold"));
  w.addSpacer(6);
  text(w, "Нет связи с сервером", rounded(13, "medium"), C.secondary, 2);
  text(w, message, Font.systemFont(11), C.secondary, 3);
  w.addSpacer();
}

async function build() {
  const w = new ListWidget();
  w.url = SERVER;
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
  const lock = family.startsWith("accessory");
  if (lock) w.setPadding(0, 0, 0, 0);
  else w.backgroundColor = C.bg;
  let data;
  try {
    data = await load();
  } catch (e) {
    if (lock) text(w, "Дробилки: нет связи", Font.systemFont(12));
    else errorWidget(w, e.message);
    return w;
  }
  const now = data.time;
  const crushers = data.crushers;

  // --- экран блокировки (система красит в один цвет) ---
  if (family === "accessoryInline") {
    text(w, crushers.map((c) => shortName(c) + " " + c.label.toLowerCase()).join(" · "), Font.systemFont(12));
    return w;
  }
  if (family === "accessoryCircular") {
    const working = crushers.filter((c) => c.state === "running").length;
    const worst = crushers.find((c) => c.state === "idle") || crushers.find((c) => c.state !== "running");
    w.addSpacer();
    const top = w.addStack();
    top.addSpacer();
    symbol(top, worst ? look(worst).symbol : "gearshape.2.fill", 16, Color.white());
    top.addSpacer();
    const mid = w.addStack();
    mid.addSpacer();
    text(mid, working + "/" + crushers.length, rounded(18, "bold"));
    mid.addSpacer();
    w.addSpacer();
    return w;
  }
  if (family === "accessoryRectangular") {
    for (const c of crushers.slice(0, 3)) {
      const row = w.addStack();
      row.centerAlignContent();
      symbol(row, look(c).symbol, 12, Color.white());
      row.addSpacer(5);
      text(row, shortName(c) + " " + c.label.toLowerCase(), rounded(13, "semibold"));
      const since = brief(c, now);
      if (since) {
        row.addSpacer(4);
        text(row, "· " + since, rounded(13, "medium"), new Color("#FFFFFF", 0.7));
      }
    }
    return w;
  }

  const size = widgetSize();

  // --- малый: две строки «значок · название · состояние» ---
  if (family === "small") {
    const roomy = size.height >= 150;  // iPhone SE: без строки «сколько уже»
    w.setPadding(14, 14, 14, 14);
    header(w, now, true);
    w.addSpacer();
    crushers.forEach((c, i) => {
      if (i) w.addSpacer(10);
      const row = w.addStack();
      row.centerAlignContent();
      badge(row, c, 30);
      row.addSpacer(8);
      const col = row.addStack();
      col.layoutVertically();
      text(col, c.name, rounded(14, "semibold"));
      text(col, c.label, rounded(12, "semibold"), look(c).color);
      const since = sinceText(c, now);
      if (since && roomy) text(col, since, rounded(11, "medium"), C.secondary);
    });
    w.addSpacer();
    return w;
  }

  // --- средний: две плитки, как в «Доме» ---
  if (family === "medium") {
    const pad = 12, gap = 10;
    w.setPadding(pad, pad, pad, pad);
    const tileW = (size.width - pad * 2 - gap) / 2, tileH = size.height - pad * 2;
    const row = w.addStack();
    crushers.slice(0, 2).forEach((c, i) => {
      if (i) row.addSpacer(gap);
      const tile = fixed(row, tileW, tileH);
      tile.url = SERVER + "/crusher?id=" + encodeURIComponent(c.id);
      tile.cornerRadius = 16;
      tile.backgroundColor = C.card;
      tile.layoutVertically();
      tile.setPadding(10, 11, 10, 11);
      badge(tile, c, 28);
      tile.addSpacer();
      text(tile, c.name, rounded(15, "semibold"));
      text(tile, c.label, rounded(13, "semibold"), look(c).color);
      const since = sinceText(c, now);
      text(tile, since ? "уже " + since : " ", rounded(11, "medium"), C.secondary);
      tile.addSpacer(6);
      dayBar(tile, c.today, tileW - 22, 5);
    });
    return w;
  }

  // --- большой (iPhone) и очень большой (iPad): кадры камер и сутки ---
  const extra = family === "extraLarge";
  const pad = 16, gap = extra ? 16 : 10;
  w.setPadding(pad, pad, pad, pad);
  const inner = size.width - pad * 2;
  const shown = crushers.slice(0, 2);
  const tileW = (inner - gap * (shown.length - 1)) / shown.length;
  // Высота кадров — всё, что остаётся после шапки (16 + 10) и итогов дня: iPad — полоса и подпись под кадром
  // и две строки (10 + 8 + 5 + 16 + 3 + 15), iPhone — «СЕГОДНЯ» (14 + 15) и по строке на дробилку (8 + 16 + 6 + 8 + 4 + 14).
  const below = extra ? 10 + 8 + 5 + 16 + 3 + 15 : 29 + shown.length * 56;
  const tileH = Math.floor(Math.min(extra ? tileW * 0.75 : tileW, size.height - pad * 2 - 26 - below));
  const images = await Promise.all(shown.map((c) => photo(c, tileW)));

  header(w, now, false);
  w.addSpacer(10);
  const tiles = w.addStack();
  shown.forEach((c, i) => {
    if (i) tiles.addSpacer(gap);
    const col = fixed(tiles, tileW, 0);  // высота 0 — по содержимому; ширина — ровно плитка: подписи не раздвигают
    col.layoutVertically();
    photoTile(col, c, images[i], tileW, tileH, now);
    if (extra) {  // iPad: сутки — под своим кадром
      col.addSpacer(10);
      dayBar(col, c.today, tileW, 8);
      col.addSpacer(5);
      text(col, "Работала " + hours(worked(c.today)), rounded(13, "semibold"));
      col.addSpacer(3);
      text(col, restText(c.today), rounded(12, "medium"), C.secondary);
    }
  });
  if (!extra) {
    w.addSpacer(14);
    text(w, "СЕГОДНЯ", rounded(12, "semibold"), C.secondary);
    for (const c of shown) {
      w.addSpacer(8);
      const line = w.addStack();
      line.centerAlignContent();
      text(line, c.name, rounded(13, "semibold"));
      line.addSpacer();
      text(line, "работала " + hours(worked(c.today)), rounded(13, "semibold"), C.secondary);
      w.addSpacer(6);
      dayBar(w, c.today, inner, 8);
      w.addSpacer(4);
      text(w, restText(c.today), rounded(11, "medium"), C.secondary);
    }
  }
  w.addSpacer();
  return w;
}

const widget = await build();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else if (Device.isPad()) {
  await widget.presentExtraLarge();
} else {
  await widget.presentLarge();
}
Script.complete();
`;
}
