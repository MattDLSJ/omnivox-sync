"""Read the i+ Interactif digital textbooks into the course folders.

What the platform actually is
-----------------------------
iplusinteractif.com (TC Média / Chenelière) serves each textbook page as a
rendered PNG. There is no text layer anywhere: the page API returns only image
paths, and the reader has no print, export or download control of any kind.

    /bibliotheque/api/accesses                     -> the books this account owns
    /cached/books/{bookId}                         -> carries a /books/{bookId}/{volumeId} link
    /api/pages/subchapters/chapters/from-volume/N  -> every page, with its chapter
    /media/iplus/book_content/{path}               -> the page itself, a PNG on S3

The images are NOT encrypted and there is no token to forge. S3 refuses a
request with no Referer, which is ordinary hotlink protection, so this asks for
them exactly as the browser does: same session, same Referer, same URL. That is
the line this module stays on. If the platform ever moves to encrypted or
signed assets, this should stop working and should NOT be made to work again.

Why chapters and not whole books
--------------------------------
Partly law: reproducing a chapter for private study is what fair dealing
covers, and mirroring an entire commercial textbook is not.

Partly arithmetic, which happens to agree. Histoire du monde is 436 pages at
roughly 1 MB each; the whole book is far past NotebookLM's 200 MB source cap,
while a chapter is 10-25 MB and lands comfortably. One notebook source per
chapter is also simply more useful than one 436-page slab.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.common import Config, ConfigError, load_config, setup_logging

REPO_ROOT = Path(__file__).resolve().parents[1]

BASE = "https://www.iplusinteractif.com"
LIBRARY = f"{BASE}/modules/bibliotheque/?section=accueil&lang=fr"

#: The medium render, 1350x1631. Verified legible down to figure captions and
#: map keys, and a third the bytes of the 2700px original, which matters
#: against a 200 MB cap.
MEDIUM_SUFFIX = "_m"

_BOOK_VOLUME = re.compile(r"/books/(\d+)/(\d+)")


class IplusError(RuntimeError):
    pass


@dataclass(frozen=True)
class Book:
    book_id: int
    title: str
    expires: str = ""


@dataclass(frozen=True)
class Page:
    page_id: int
    name: str          # "C1", "IV", "19" - the printed page label, not an index
    index: int
    image_path: str    # e.g. "2197/19_1682545605.png"
    chapter_id: int | None


def credentials(repo_root: Path) -> tuple[str, str]:
    """CHENELIERE_USER / CHENELIERE_PASS from .env, same as the Omnivox pair."""
    from dotenv import dotenv_values

    values = dotenv_values(Path(repo_root) / ".env")
    out = []
    for key in ("CHENELIERE_USER", "CHENELIERE_PASS"):
        value = (values.get(key) or "").strip()
        if not value:
            raise ConfigError(f"{key} is missing from .env. Run: make setup-cheneliere")
        out.append(value)
    return out[0], out[1]


class IplusSession:
    """A logged-in browser context. Reuses its profile, so login is rare."""

    def __init__(self, repo_root: Path, *, headed: bool = False, logger=None) -> None:
        self.repo_root = Path(repo_root)
        self.headed = headed
        self.log = logger or logging.getLogger("school.iplus")
        self._pw = None
        self._ctx = None
        self.page = None

    def __enter__(self) -> "IplusSession":
        from playwright.sync_api import sync_playwright

        profile = self.repo_root / "state" / "iplus-profile"
        profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=not self.headed, locale="fr-CA",
            viewport={"width": 1600, "height": 1100},
        )
        self._ctx.set_default_timeout(45000)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc) -> bool:
        for closer in (getattr(self._ctx, "close", None), getattr(self._pw, "stop", None)):
            try:
                if closer:
                    closer()
            except Exception:  # noqa: BLE001
                pass
        return False

    # -- login ----------------------------------------------------------

    def login(self) -> None:
        """Log in if the stored profile is not already signed in."""
        user, password = credentials(self.repo_root)
        self.page.goto(LIBRARY, wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)
        if "auth.tcmedialivres.com" not in self.page.url:
            self.log.info("Already signed in to i+ Interactif")
            return
        # One attempt only. Repeatedly posting a wrong password to someone's
        # account is how the account gets locked.
        self.page.fill("#loginId", user)
        self.page.fill("#password", password)
        self.page.press("#password", "Enter")
        self.page.wait_for_timeout(7000)
        if "auth.tcmedialivres.com" in self.page.url:
            body = (self.page.inner_text("body") or "").lower()
            if any(w in body for w in ("captcha", "robot", "vérification")):
                raise IplusError(
                    "i+ is showing a human-verification challenge. Run with --headed, "
                    "solve it yourself once, and the saved profile will be reused."
                )
            raise IplusError("i+ login failed. Check: make setup-cheneliere")
        self.log.info("Signed in to i+ Interactif")

    # -- catalogue ------------------------------------------------------

    def _json(self, path: str, referer: str) -> dict | list:
        resp = self._ctx.request.get(BASE + path, headers={"referer": referer})
        if resp.status != 200:
            raise IplusError(f"{path} returned HTTP {resp.status}")
        return resp.json()

    def books(self) -> list[Book]:
        data = self._json("/bibliotheque/api/accesses", LIBRARY)
        accesses = (data or {}).get("data", {}).get("accesses", []) if isinstance(data, dict) else []
        out: list[Book] = []
        for access in accesses:
            sub = (access or {}).get("subproduct") or {}
            book_id = ((sub.get("subproductAccessRightServices") or {}).get("bookId"))
            title = (sub.get("product") or {}).get("productName") or sub.get("subproductName") or ""
            if book_id and title:
                out.append(Book(int(book_id), title.strip(),
                                str(access.get("accessExpirationDate") or "")))
        # The same book can be granted by more than one code.
        seen, unique = set(), []
        for b in out:
            if b.book_id not in seen:
                seen.add(b.book_id)
                unique.append(b)
        return unique

    def volume_id(self, book_id: int) -> int:
        """A book's volume, scraped from its landing page.

        There is no API for this; the id only appears as a /books/{book}/{volume}
        link on /cached/books/{book}.
        """
        url = f"{BASE}/cached/books/{book_id}"
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(5000)
        hrefs = self.page.evaluate(
            "() => [...document.querySelectorAll('a')].map(a => a.getAttribute('href') || '')"
        )
        for href in hrefs:
            match = _BOOK_VOLUME.search(href or "")
            if match and int(match.group(1)) == book_id:
                return int(match.group(2))
        raise IplusError(f"No volume link found on {url}")

    def pages(self, volume_id: int) -> list[Page]:
        referer = f"{BASE}/books/0/{volume_id}/0"
        data = self._json(
            f"/api/pages/subchapters/chapters/from-volume/{volume_id}", referer
        )
        out = []
        for row in data or []:
            subs = row.get("subchapters") or [{}]
            chapter = ((subs[0] or {}).get("chapter") or {}).get("chapterId")
            out.append(Page(
                page_id=int(row["pageId"]), name=str(row.get("pageName") or ""),
                index=int(row.get("pageIndex") or 0),
                image_path=str(row.get("pageImagePath") or ""),
                chapter_id=int(chapter) if chapter else None,
            ))
        return out

    def fetch_page_image(self, page: Page, volume_id: int, *, medium: bool = True) -> bytes:
        """One page PNG, requested the way the reader requests it."""
        path = page.image_path
        if medium:
            path = path.replace(".png", f"{MEDIUM_SUFFIX}.png")
        referer = f"{BASE}/books/0/{volume_id}/{page.page_id}"
        resp = self._ctx.request.get(
            f"{BASE}/media/iplus/book_content/{path}", headers={"referer": referer}
        )
        if resp.status != 200:
            raise IplusError(f"page {page.name}: HTTP {resp.status}")
        return resp.body()


# --------------------------------------------------------------------------
# Selection and assembly
# --------------------------------------------------------------------------


def select_pages(pages: list[Page], *, chapter: int | None, first: str | None,
                 last: str | None) -> list[Page]:
    """Pages for one chapter, or an inclusive run between two printed labels.

    Labels, not indices: a textbook's front matter is numbered C1, I, II, and
    asking for "pages 40 to 72" means the numbers printed on the pages.
    """
    if chapter is not None:
        ids = sorted({p.chapter_id for p in pages if p.chapter_id is not None})
        if chapter < 1 or chapter > len(ids):
            raise IplusError(f"chapter {chapter} is outside 1..{len(ids)}")
        wanted = ids[chapter - 1]
        return [p for p in pages if p.chapter_id == wanted]

    if first is None:
        raise IplusError("give either --chapter or --pages")
    names = [p.name for p in pages]
    if first not in names:
        raise IplusError(f"no page labelled {first!r}")
    start = names.index(first)
    end = names.index(last) if last and last in names else start
    if end < start:
        start, end = end, start
    return pages[start:end + 1]


def build_pdf(images: list[bytes], dest: Path) -> Path:
    """Assemble page PNGs into one PDF, losslessly.

    img2pdf embeds the PNG data as-is. Pillow would re-encode to JPEG and put
    artefacts on the small type in figure captions, which is exactly the text
    that has to survive for the notebook to be able to quote it.
    """
    import img2pdf

    if not images:
        raise IplusError("no pages to write")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".pdf.part")
    tmp.write_bytes(img2pdf.convert(images))
    tmp.replace(dest)
    return dest


def find_book(books: list[Book], wanted: str) -> Book | None:
    low = wanted.casefold().strip()
    exact = [b for b in books if b.title.casefold() == low]
    if exact:
        return exact[0]
    partial = [b for b in books if low in b.title.casefold()]
    return partial[0] if len(partial) == 1 else None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


#: NotebookLM refuses a source over 200 MB. Stay under it with room to spare
#: rather than discovering the refusal after a long upload.
MAX_SOURCE_MB = 190.0


def queue_for_notebook(cfg: Config, course, pdf: Path, log: logging.Logger) -> bool:
    """Put the chapter in the same queue the sync and the recorder feed.

    Returns False rather than raising if the lock cannot be taken: the PDF is
    already on disk, and `make upload` will pick it up. Losing the queue entry
    is recoverable; losing the chapter is not.
    """
    from src.common import RunBusy, StateStore, run_lock

    store = StateStore(cfg.repo_root / "state" / "upload_queue.json")
    record = {
        "course_code": course.code,
        "notebook": course.notebook,
        "path": str(pdf),
        "filename": pdf.name,
        "queued_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        with run_lock(cfg.repo_root / "state" / "queue.lock", wait=30):
            store.write(store.read() + [record])
    except RunBusy:
        log.error("Could not take state/queue.lock in 30s; %s not queued", pdf.name)
        return False
    log.info("Queued %s for %s", pdf.name, course.notebook)
    return True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.iplus",
        description="Save a chapter of an i+ Interactif textbook into its course folder.",
    )
    p.add_argument("--config", default=str(REPO_ROOT / "config.yaml"))
    p.add_argument("--list", action="store_true", help="show the books this account owns")
    p.add_argument("--book", help="book title, or enough of it to be unambiguous")
    p.add_argument("--chapters", action="store_true", help="list the chapters of --book")
    p.add_argument("--chapter", type=int, help="which chapter to save")
    p.add_argument("--pages", help="a run of printed page labels instead, e.g. 40-72")
    p.add_argument("--course", help="course code the PDF belongs to; defaults by title match")
    p.add_argument("--full-size", action="store_true", help="2700px pages instead of 1350px")
    p.add_argument("--headed", action="store_true", help="show the browser, to solve a challenge")
    p.add_argument("--no-upload", action="store_true",
                   help="write the PDF but do not queue it for NotebookLM")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2
    log = setup_logging("iplus", cfg.repo_root)

    with IplusSession(cfg.repo_root, headed=args.headed, logger=log) as session:
        session.login()
        books = session.books()

        if args.list or not args.book:
            print("Books on this account:")
            for b in books:
                print(f"  {b.book_id:>6}  {b.title}")
            if not args.book:
                print("\nPick one with --book, then --chapters to see its chapters.")
            return 0

        book = find_book(books, args.book)
        if book is None:
            print(f"No single book matches {args.book!r}.", file=sys.stderr)
            for b in books:
                print(f"  {b.title}", file=sys.stderr)
            return 1

        volume = session.volume_id(book.book_id)
        pages = session.pages(volume)
        log.info("%s: volume %d, %d pages", book.title, volume, len(pages))

        if args.chapters or (args.chapter is None and not args.pages):
            ids = sorted({p.chapter_id for p in pages if p.chapter_id is not None})
            print(f"{book.title}: {len(ids)} chapters")
            for n, cid in enumerate(ids, 1):
                owned = [p for p in pages if p.chapter_id == cid]
                print(f"  chapter {n:>2}  {len(owned):>3} pages   "
                      f"{owned[0].name} .. {owned[-1].name}")
            return 0

        first = last = None
        if args.pages:
            bits = args.pages.split("-", 1)
            first, last = bits[0].strip(), (bits[1].strip() if len(bits) > 1 else None)
        chosen = select_pages(pages, chapter=args.chapter, first=first, last=last)
        log.info("Fetching %d pages (%s..%s)", len(chosen), chosen[0].name, chosen[-1].name)

        images = []
        for page in chosen:
            images.append(session.fetch_page_image(page, volume, medium=not args.full_size))
            log.info("  page %s (%.0f kB)", page.name, len(images[-1]) / 1000)

        course = cfg.course_by_code(args.course) if args.course else None
        if course is None:
            course = next(
                (c for c in cfg.courses
                 if c.folder.casefold()[:12] in book.title.casefold()
                 or book.title.casefold()[:12] in c.folder.casefold()),
                None,
            )
        folder = cfg.folder_for(course) if course else cfg.base_path
        if course is not None and cfg.books_folder:
            folder = folder / cfg.books_folder
            folder.mkdir(parents=True, exist_ok=True)
        # Always name by the PRINTED page range. The chapter index here counts
        # groups in the manifest, and group 1 is the front matter, so "chapter 2"
        # is the book's Chapitre 1. The page numbers are the ones on the paper
        # and cannot drift.
        span = f"p{chosen[0].name}-{chosen[-1].name}"
        label = f"ch{args.chapter} {span}" if args.chapter else span
        dest = folder / f"{book.title} - {label}.pdf"
        build_pdf(images, dest)
        size = dest.stat().st_size / 1e6
        print(f"{dest}  ({len(images)} pages, {size:.0f} MB)")

        if size > MAX_SOURCE_MB:
            print(f"  Not queued: {size:.0f} MB is over NotebookLM's {MAX_SOURCE_MB:.0f} MB "
                  "source cap. Take a smaller range and it will queue.")
        elif course is None:
            print("  Not queued: no course matched this book. Re-run with --course CODE.")
        elif not args.no_upload:
            queued = queue_for_notebook(cfg, course, dest, log)
            print(f"  Queued for {course.notebook}" if queued else
                  "  NOT queued: could not take state/queue.lock; run `make upload` later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
