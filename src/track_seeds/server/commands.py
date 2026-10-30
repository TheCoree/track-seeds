"""Команды для сервера без рабочего стола: запуск, дробилки, пароль камеры, проверка, голос — всё из консоли."""

import argparse
import getpass
import ipaddress
import logging
import sys
import time
from pathlib import Path

from pydantic import SecretStr

from ..camera import mask_url
from ..config import (
    LOOPBACK,
    DetectorConfig,
    NotifyConfig,
    NtfyConfig,
    ServerSettings,
    Settings,
)
from .store import ConfigStore, GlobalSettings

log = logging.getLogger("track_seeds")

RESTART_HINT = "Перезапустите сервер, чтобы он подхватил изменения: docker compose restart (или Ctrl+C и make serve)"


def open_store(settings: Settings, create: bool = True) -> ConfigStore:
    """CONFIG_DIR: при первом запуске создаётся (уведомления — из .env), битый — понятная ошибка."""
    store = ConfigStore(settings.app.config_dir)
    if not store.exists():
        if not create:
            sys.exit(f"Настроек ещё нет: {store.root} (запустите track-seeds serve)")
        store.save_settings(GlobalSettings(
            notify=NotifyConfig.model_validate(settings.notify.model_dump()),
            ntfy=NtfyConfig.model_validate(settings.ntfy.model_dump()),
            detector=DetectorConfig.model_validate(settings.detector.model_dump()),
        ))
        log.info("Созданы общие настройки %s (уведомления — из .env)", store.settings_path)
    try:
        moved = store.load()
    except ValueError as e:
        sys.exit(f"Не читается {store.settings_path}: {e}\nПредыдущая версия — {store.settings_path}.bak")
    for line in moved:
        log.info("Перенесено: %s", line)
    for crusher_id, error in store.errors.items():
        log.error("Настройки дробилки %s не прочитались: %s", crusher_id, error)
    return store


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app
    from .auth import AccountStore, AuthError
    from .workers import Supervisor

    web = ServerSettings(_env_file=args.env_file if Path(args.env_file).is_file() else None)
    host, port = args.host or web.host, args.port or web.port
    password = web.password.get_secret_value() if web.password else None
    accounts = AccountStore(settings.app.data_dir)
    if not password and not accounts.has_admin() and not _is_loopback(host) and not web.allow_no_password:
        sys.exit(f"Веб-интерфейс открыт для сети ({host}), но разработчик не заведён. Задайте WEB_PASSWORD в .env "
                 "(вход: admin и этот пароль), заведите его командой track-seeds admin "
                 "или оставьте WEB_HOST=127.0.0.1 (тогда — только с этого компьютера "
                 "или через ssh -L 8080:localhost:8080).")
    store = open_store(settings)
    static_dir = web.static_dir or Path(__file__).resolve().parents[3] / "web" / "out"
    supervisor = Supervisor(store, settings.app.data_dir, web.events_keep_days)
    try:
        app = create_app(supervisor, static_dir, password, manage=True, allowed_networks=web.allowed_networks,
                         admin_networks=web.admin_networks, trusted_proxies=web.trusted_proxies,
                         open_networks=web.admin_networks if web.allow_no_password else LOOPBACK,
                         public_url=web.public_url, accounts=accounts)
    except AuthError as e:
        sys.exit(f"WEB_PASSWORD в .env: {e}")
    log.info("Дробилки: %s; настройки на месте — %s", ", ".join(c.name for c in store.ordered()),
             store.crushers_dir.resolve())
    log.info("Веб-интерфейс: http://%s:%d/%s", "127.0.0.1" if host == "0.0.0.0" else host, port,
             "" if (static_dir / "index.html").is_file() else "  (фронтенд не собран: make web)")
    if not accounts.has_admin():
        log.info("Вход без пароля — только с этого компьютера; завести разработчика: «Мой аккаунт» в вебе "
                 "или track-seeds admin")
    if not _is_loopback(host):
        log.info("Сайт — из сетей: %s; панель разработчика — из: %s",
                 "любых" if "0.0.0.0/0" in web.allowed_networks else ", ".join(web.allowed_networks),
                 ", ".join(web.admin_networks))
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False, timeout_graceful_shutdown=5,
                proxy_headers=False)
    return 0


def cmd_admin(settings: Settings, args: argparse.Namespace) -> int:
    """Завести разработчика или сменить ему пароль; --no-2fa — телефон с кодами потерян."""
    from .auth import AccountStore, AuthError

    accounts = AccountStore(settings.app.data_dir)
    user = accounts.by_login(args.login)
    if args.no_2fa:
        if user is None:
            sys.exit(f"Нет пользователя {args.login}")
        accounts.totp_reset(user["id"])
        accounts.revoke_sessions(user["id"])
        accounts.record("totp_off", True, user["id"], user["username"], detail="из консоли")
        print(f"✓ {user['username']}: вход без кода 2FA, все сессии закрыты")
        return 0
    if user is not None and user["role"] != "admin":
        sys.exit(f"{args.login} — клиент, а не разработчик")
    if not sys.stdin.isatty():
        sys.exit("Пароль спрашивается в терминале — запустите команду интерактивно (в Docker — с ключом -it)")
    password = getpass.getpass(f"Новый пароль для {args.login}: ")
    if password != getpass.getpass("Ещё раз: "):
        sys.exit("Пароли не совпали — ничего не изменено")
    try:
        if user is None:
            user = accounts.create_user(args.login, "Разработчик", password, "admin")
            accounts.record("setup", True, user["id"], user["username"], detail="из консоли")
            print(f"✓ Разработчик {user['username']} заведён")
        else:
            accounts.set_password(user["id"], password)
            accounts.update_user(user["id"], disabled=False)
            accounts.record("password", True, user["id"], user["username"], detail="из консоли")
            print(f"✓ Пароль {user['username']} сменён, его сессии закрыты")
    except AuthError as e:
        sys.exit(f"✗ {e}")
    return 0


def cmd_users(settings: Settings, args: argparse.Namespace) -> int:
    from .auth import AccountStore

    accounts = AccountStore(settings.app.data_dir)
    rows = accounts.users()
    if not rows:
        print("Аккаунтов нет: вход без пароля — только с этого компьютера. Завести разработчика: track-seeds admin")
        return 0
    for u in rows:
        seen = time.strftime("%d.%m.%Y %H:%M", time.localtime(u["last_seen"])) if u["last_seen"] else "не входил"
        flags = [x for x in ("разработчик" if u["role"] == "admin" else "клиент",
                             "2FA" if u["totp"] else "", "ОТКЛЮЧЁН" if u["disabled"] else "") if x]
        print(f"{u['username']:<20} {u['name']:<24} {', '.join(flags):<24} сессий {u['sessions']}, "
              f"виджетов {u['widgets']}, был {seen}")
    return 0


def cmd_crushers(settings: Settings, args: argparse.Namespace) -> int:
    store = open_store(settings, create=False)
    for crusher in store.ordered():
        samples = crusher.seeds.samples
        own = sum(not s.builtin for s in samples)
        full = sum("full" in s.labels.values() for s in samples)
        empty = sum("empty" in s.labels.values() for s in samples)
        speakers = ", ".join(s.name for s in crusher.speakers) or "нет"
        state = "" if crusher.enabled else " [наблюдение выключено]"
        print(f"{crusher.id:<14} {crusher.name}{state} — {mask_url(crusher.camera.url)}")
        print(f"{'':<14} зоны: {', '.join(z.name for z in crusher.seeds.zones)}; эталонов {len(samples)} "
              f"({full} есть / {empty} пусто, снятых на месте {own}); динамики: {speakers}; "
              f"пароль камеры: {'есть' if crusher.camera.password else 'НЕТ — track-seeds password ' + crusher.id}")
        if error := store.errors.get(crusher.id):
            print(f"{'':<14} ✗ настройки не прочитались, работает с настройками из программы: {error}")
    print(f"\nНастройки на месте: {store.crushers_dir.resolve()}")
    return 1 if store.errors else 0


def cmd_password(settings: Settings, args: argparse.Namespace) -> int:
    """Пароль камеры дробилки — на сервере без браузера (в Docker: docker compose run --rm -it …)."""
    store = open_store(settings)
    ids = [args.id] if args.id else list(store.crushers)
    if args.id and args.id not in store.crushers:
        sys.exit(f"Нет дробилки {args.id} (есть: {', '.join(store.crushers)})")
    if not sys.stdin.isatty():
        sys.exit("Пароль спрашивается в терминале — запустите команду интерактивно (в Docker — с ключом -it)")
    password = getpass.getpass(f"Пароль камер ({', '.join(store.crushers[i].name for i in ids)}): ")
    if not password:
        sys.exit("Пустой пароль — ничего не изменено")
    for crusher_id in ids:
        store.update(crusher_id, lambda c: setattr(c.camera, "password", SecretStr(password)))
        print(f"✓ {store.crushers[crusher_id].name}")
    print(RESTART_HINT)
    return 0


def cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    """Проверить без браузера: камеры, зоны на текущем кадре, динамики (без звука), голос, уведомления."""
    from ..analyzers.seeds import SeedsAnalyzer
    from ..camera import probe_camera
    from ..seeds import confidence
    from ..voice import SpeakerError, Synthesizer, build_speaker

    store = open_store(settings, create=False)
    crushers = [store.crushers[args.id]] if args.id in store.crushers else store.ordered()
    if args.id and args.id not in store.crushers:
        sys.exit(f"Нет дробилки {args.id} (есть: {', '.join(store.crushers)})")
    problems = len(store.errors)
    for crusher_id, error in store.errors.items():
        print(f"✗ Настройки дробилки {crusher_id} не прочитались — она работает с настройками из программы: {error}")
    reasons = {"no_samples": "нет эталонов", "dark": "темно", "novel": "не похоже на эталоны", "border": "на границе",
               "people": "закрыта человеком"}
    for crusher in crushers:
        print(f"\n== {crusher.name} ({crusher.id}){'' if crusher.enabled else ' — выключена'}")
        result = probe_camera(crusher.camera, 2)
        if not result.ok:
            problems += 1
            print(f"  ✗ камера: {result.error}")
        else:
            print(f"  ✓ камера: {result.width}×{result.height}, {result.measured_fps:.0f} к/с")
            if not crusher.seeds.zones:
                problems += 1
                print("  ✗ зоны не нарисованы")
            analyzer = SeedsAnalyzer(crusher.seeds, crusher.name, store.crusher_dir(crusher.id),
                                     builtin_dir=store.specs[crusher.id].builtin_dir)
            for zone in analyzer.evaluate(result.image):
                text = {"covered": "семена есть", "uncovered": "ПУСТО"}.get(zone["reading"],
                                                                          reasons.get(zone["reason"], "не определено"))
                score = ""
                if zone["score"] is not None:
                    sure = confidence(zone["score"], crusher.seeds.threshold)
                    sure = sure if zone["reading"] == "covered" else 1 - sure
                    score = f", уверенность {sure:.1%}".replace(".", ",")
                hint = "" if zone["empty"] else " — эталона «пусто» пока нет"
                mark = "✓" if zone["reading"] != "unknown" else "!"
                print(f"  {mark} зона «{zone['name']}»: {text}{score} (эталоны {zone['full']} есть / "
                      f"{zone['empty']} пусто){hint}")
            if result.first_image is not None and result.first_image is not result.image:
                for drive in analyzer.evaluate_drives(result.first_image, result.image):
                    if drive["motion"] is None:
                        print(f"  ! привод «{drive['name']}»: не определить (темно)")
                    else:
                        state = "крутится — дробилка работает" if drive["moving"] else "неподвижен — дробилка стоит"
                        print(f"  ✓ привод «{drive['name']}»: {state} (за пробу изменилось {drive['motion']:.0%})")
            elif any(z.kind == "motion" for z in crusher.seeds.zones):
                print("  ! привод: один кадр — движение не оценить")
        if crusher.camera.username and not crusher.camera.password:
            problems += 1
            print("  ✗ пароль камеры не задан — сервер не будет к ней подключаться")
        for config in crusher.speakers:
            speaker = build_speaker(config)
            try:
                info = speaker.probe()  # type: ignore[attr-defined]
                print(f"  ✓ динамик «{config.name}»: {info.get('codec') or info.get('player')}")
            except SpeakerError as e:
                problems += 1
                print(f"  ✗ динамик «{config.name}»: {e}")
        if not crusher.speakers:
            print("  · динамиков нет — только уведомления")
    voice = store.settings.voice
    try:
        synth = Synthesizer(voice, store.settings.detector.models_dir, settings.app.data_dir / "voice")
        synth.prepare()
        print(f"\n{'✓' if synth.state == 'ready' else '!'} голос: {synth.voice} ({synth.engine})"
              f"{' — ' + synth.error if synth.error else ''}")
    except Exception as e:
        problems += 1
        print(f"\n✗ голос: {e}")
    ntfy = store.settings.ntfy
    channels = store.settings.notify.channels
    if "ntfy" in channels:
        print(f"{'✓' if ntfy.topic else '✗'} ntfy: {ntfy.url}/{'…' if ntfy.topic else '(топик не задан)'}")
        problems += not ntfy.topic
    else:
        print("! уведомления на телефон (ntfy) выключены")
    print(f"\n{'Всё в порядке' if not problems else f'Проблем: {problems}'}")
    return 1 if problems else 0


def cmd_say(settings: Settings, args: argparse.Namespace) -> int:
    """Сказать фразу на динамиках дробилки — проверить звук на сервере без браузера."""
    from ..voice import SpeakerError, Synthesizer, build_speaker, compose

    store = open_store(settings, create=False)
    crusher = store.crushers.get(args.crusher)
    if crusher is None:
        sys.exit(f"Нет дробилки {args.crusher} (есть: {', '.join(store.crushers)})")
    speakers = [s for s in crusher.speakers if not args.speaker or s.name == args.speaker]
    if not speakers:
        sys.exit(f"У «{crusher.name}» нет такого динамика")
    voice = store.settings.voice
    synth = Synthesizer(voice, store.settings.detector.models_dir, settings.app.data_dir / "voice")
    speech, rate = synth.synthesize(args.text)
    audio, audio_rate = compose(speech, rate, voice, 1)
    failed = 0
    for config in speakers:
        try:
            build_speaker(config).play(audio, audio_rate)
            print(f"✓ {config.name}")
        except SpeakerError as e:
            failed += 1
            print(f"✗ {config.name}: {e}")
    return 1 if failed else 0


def add_commands(sub) -> None:
    p = sub.add_parser("serve", help="сервер: обе дробилки + веб-интерфейс")
    p.add_argument("--host", help="адрес (WEB_HOST, по умолчанию 127.0.0.1; другой — только с WEB_PASSWORD)")
    p.add_argument("--port", type=int, help="порт (WEB_PORT, по умолчанию 8080)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("admin", help="завести разработчика или сменить ему пароль (спросит в терминале)")
    p.add_argument("login", nargs="?", default="admin", help="логин (по умолчанию admin)")
    p.add_argument("--no-2fa", action="store_true", help="отключить вход по коду 2FA (телефон потерян)")
    p.set_defaults(func=cmd_admin)

    p = sub.add_parser("users", help="аккаунты веб-интерфейса: разработчики и клиенты")
    p.set_defaults(func=cmd_users)

    p = sub.add_parser("crushers", help="дробилки: камеры, зоны, эталоны, динамики")
    p.set_defaults(func=cmd_crushers)

    p = sub.add_parser("password", help="задать пароль камеры дробилки (спросит в терминале)")
    p.add_argument("id", nargs="?", help="одна дробилка (по умолчанию — один пароль для всех)")
    p.set_defaults(func=cmd_password)

    p = sub.add_parser("check", help="проверить без браузера: камеры, зоны, динамики, голос, уведомления")
    p.add_argument("id", nargs="?", help="одна дробилка (по умолчанию все)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("say", help="сказать фразу на динамиках дробилки")
    p.add_argument("text")
    p.add_argument("--crusher", required=True, help="drobilka-1 или drobilka-2")
    p.add_argument("--speaker", help="только этот динамик (по названию)")
    p.set_defaults(func=cmd_say)
