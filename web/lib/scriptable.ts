/**
 * Виджет для iPhone (приложение Scriptable): состояние дробилок и итоги дня. Ключ — только чтение
 * /api/widget, отзывается в «Мой аккаунт». Размеры: малый, средний, большой (с кадрами камер),
 * на экран блокировки — прямоугольный и строкой.
 */
export function scriptableWidget(server: string, token: string): string {
  return `// track-seeds — дробилки на экране iPhone (Scriptable)
// Ключ только для чтения состояния; отозвать — на сайте: «Мой аккаунт» → «Виджет».
const SERVER = ${JSON.stringify(server)};
const TOKEN = ${JSON.stringify(token)};

const COLORS = {
  running: "#10b981", // работает
  idle: "#f59e0b",    // крутится без зерна
  stopped: "#0ea5e9", // стоит
  offline: "#94a3b8", // нет связи
  unknown: "#94a3b8",
};
const family = config.widgetFamily || "medium";
const bg = Color.dynamic(new Color("#ffffff"), new Color("#1c1c1e"));
const fg = Color.dynamic(new Color("#111111"), new Color("#f5f5f5"));
const muted = Color.dynamic(new Color("#6b7280"), new Color("#a1a1aa"));

function hours(seconds) {
  const m = Math.round((seconds || 0) / 60);
  return m >= 60 ? Math.floor(m / 60) + " ч " + (m % 60) + " мин" : m + " мин";
}

function since(ts, now) {
  return ts ? hours(now - ts) : "";
}

async function api(path) {
  const req = new Request(SERVER + path);
  req.headers = { Authorization: "Bearer " + TOKEN };
  req.timeoutInterval = 15;
  return req;
}

async function load() {
  const req = await api("/api/widget");
  const data = await req.loadJSON();
  if (req.response.statusCode !== 200) throw new Error(data.detail || "Ошибка " + req.response.statusCode);
  return data;
}

function dot(stack, color, size) {
  const symbol = SFSymbol.named("circle.fill");
  const image = stack.addImage(symbol.image);
  image.tintColor = new Color(color);
  image.imageSize = new Size(size, size);
}

function text(stack, value, font, color, lines) {
  const t = stack.addText(value);
  t.font = font;
  t.textColor = color || fg;
  t.lineLimit = lines || 1;
  t.minimumScaleFactor = 0.7;
  return t;
}

async function build() {
  const w = new ListWidget();
  w.url = SERVER;
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
  let data;
  try {
    data = await load();
  } catch (e) {
    w.backgroundColor = bg;
    text(w, "Дробилки", Font.semiboldSystemFont(14));
    w.addSpacer(4);
    text(w, "Нет связи: " + e.message, Font.systemFont(12), muted, 3);
    return w;
  }
  const now = data.time;
  const crushers = data.crushers;

  if (family === "accessoryInline") {
    text(w, crushers.map((c) => c.name.replace("Дробилка ", "Д") + ": " + c.label.toLowerCase()).join(" · "), Font.systemFont(12));
    return w;
  }
  if (family === "accessoryRectangular" || family === "accessoryCircular") {
    for (const c of crushers) {
      text(w, c.name + ": " + c.label.toLowerCase(), Font.semiboldSystemFont(12));
    }
    return w;
  }

  w.backgroundColor = bg;
  w.setPadding(14, 14, 12, 14);
  const head = w.addStack();
  head.centerAlignContent();
  text(head, "Дробилки", Font.boldSystemFont(family === "small" ? 13 : 15));
  head.addSpacer();
  const clock = new DateFormatter();
  clock.dateFormat = "HH:mm";
  text(head, clock.string(new Date(now * 1000)), Font.systemFont(11), muted);
  w.addSpacer(family === "small" ? 6 : 8);

  for (const c of crushers) {
    const row = w.addStack();
    row.centerAlignContent();
    dot(row, COLORS[c.state] || COLORS.offline, family === "small" ? 9 : 11);
    row.addSpacer(6);
    const col = row.addStack();
    col.layoutVertically();
    const title = col.addStack();
    text(title, c.name, Font.semiboldSystemFont(family === "small" ? 12 : 14));
    if (family !== "small") {
      title.addSpacer(6);
      text(title, c.label + (c.since ? " · " + since(c.since, now) : ""), Font.systemFont(13), muted);
    } else {
      text(col, c.label, Font.systemFont(11), muted);
    }
    if (family !== "small" && c.today) {
      text(col, "Сегодня: работала " + hours(c.today.running + c.today.idle) + ", стояла " + hours(c.today.stopped) +
        (c.today.stops ? " (" + c.today.stops + " ост.)" : ""), Font.systemFont(11), muted);
    }
    if (family === "large") {
      try {
        const img = await (await api("/api/widget/" + c.id + ".jpg")).loadImage();
        col.addSpacer(4);
        const pic = col.addImage(img);
        pic.imageSize = new Size(213, 120);
        pic.cornerRadius = 8;
      } catch (e) {}
    }
    w.addSpacer(family === "small" ? 6 : 10);
  }
  w.addSpacer();
  return w;
}

const widget = await build();
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
`;
}
