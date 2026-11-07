# Cyph Study mode: design and plan

Status: agreed scope, not built. Written from the owner's own answers (2026-11-07). Anything not decided is under
**Open items** and is not built until it is.

Replaces the one-line "Phase 6" in `plans/2026-10-24-cyph-full-app.md` and the unwritten "Spec 3" in
`specs/2026-10-23-cyph-companion-core-design.md`. The Architect mode (project files) stays in its own later spec.

## 1. What it is

Study mode turns Cyph into a study partner for one student: **Sec 3 now, O-Level next year (Sec 4)**. Seven subjects,
all managed from the app (add, rename, archive): **Math, Chemistry, Physics, Amath, English, SS, Geog**.

Three areas, in this order of importance to the owner:

1. **Subjects and notes.** Each subject holds typed notes and imported sources (PDFs and the like) that Cyph turns into
   readable notes, the way Google NotebookLM does. Pen notes come later (see phase 5): page-based notebooks for class
   notes, and freeform boards in the style of Apple Freeform.
2. **Cards.** Spaced-repetition flashcards, drafted by Cyph from the owner's notes and approved by the owner.
3. **Tutoring and practice.** Cyph teaches back and forth, and runs worked problems, structured and source-based
   questions, essay and comprehension feedback, and past papers.

### What makes Cyph special here (the owner's picks)

| Edge | What it means in the build |
|---|---|
| **Knows my plan and tests** | A `test` in the Plan is linked to a subject. From it Cyph builds a revision plan and adds review blocks to the Plan. |
| **Tracks my weak spots** | Every miss (a card, a practice question, a past-paper mark) is logged against a topic. Cyph decides the topics per subject, and the owner can rename and merge them. Weak topics come back until they stick. |
| **Nudges me at the right time** | The Study notification channel and the Study-due widget use real data: cards due, a test countdown, a weak topic. They obey mute and quiet hours. |
| **Works from my own notes** | He searches the owner's notes by tool, answers from them, and shows which note or page each answer came from. |

"Interactive learning" was clarified as **tutoring back and forth**: he asks follow-ups, gives hints before the answer,
and changes how he explains when the owner is stuck. Live visuals, answer-by-doing cards and games were not chosen, so
they are not in scope (see Parked).

## 2. Decisions

| Topic | Decision |
|---|---|
| Subjects | 7 to start, user-managed. |
| Level | Sec 3 now, O-Level next year. |
| PDF import | NotebookLM style: clean readable notes (headings, formulas), original kept, plus per source a summary, key terms, a study guide and likely questions. |
| Embeddings | Gemini Embedding 2, as the owner asked. The exact model id and vector size are checked against the current API docs in phase 0 and are not assumed. |
| Notes | Typed notes (rich text, tables, checklists, formulas, folder per subject, search). Pen notes on a canvas, built later. |
| Pen notes | Two separate things that share one ink engine. **Notebooks:** page-based, with blank, lined, grid and graph-paper backgrounds, for class notes. **Boards:** freeform, an endless canvas you pan and zoom, like Apple Freeform. Both have shapes and a ruler, photos, and export to PDF. |
| Board items | Pen strokes, text boxes and sticky notes, shapes and connectors that stay joined when moved, photos and PDF pages, and links to your typed notes and cards (tap to open). |
| Pen notes offline | **Pen notes only** work offline (saved on the device, synced later). Everything else needs the server. |
| iPad | A web app in Safari (Add to Home Screen), with Apple Pencil through the browser. No native app, no Mac. |
| iPad network | Through the existing **Playit** tunnel. Needs a certificate Safari accepts (see 6.2). |
| Cards | Cyph drafts from notes and the owner approves or edits. Cards can hold pictures. |
| Formulas | Maths and chemistry notation displays properly **everywhere**: notes, cards, imports, Cyph's answers. |
| Practice | Worked problems; structured and source-based questions (SS, Geog); past papers; English essay and comprehension. |
| Topics | Cyph decides them. |
| Build order | First: subjects, notes and PDF import. Second: cards and review. Pen notes last. |

## 3. Data (server, `data/core/study.db`, SQLite)

- `subjects(id, name, colour, archived)`
- `sources(id, subject, title, kind, original_path, status, added)`: a PDF, photo, markdown or pasted text.
- `source_outputs(source, kind, markdown)`: the clean notes, summary, key terms, study guide, likely questions.
- `notes(id, subject, folder, title, markdown, updated)`: typed notes (Markdown with LaTeX and chemistry).
- `chunks(id, source_or_note, page, text, embedding BLOB)`: for search with citations.
- `topics(id, subject, name)` and `topic_links` from cards, questions and chunks.
- `cards(id, subject, topic, front, back, images, source_chunk, state, due, interval, ease, reps, lapses)`;
  `state` is `draft`, `approved` or `suspended`.
- `reviews(card, at, grade, seconds)`.
- `attempts(id, subject, topic, kind, question, answer, marks, max_marks, feedback, at)`: practice and past papers.
- `tests(plan_event_id, subject, topics)`: ties a Plan test to a subject.
- Later, notebooks: `notebooks(id, subject, title)` and `pages(id, notebook, n, background, ink JSON, images)`.
- Later, boards: `boards(id, subject, title, view)` and `board_items(id, board, kind, x, y, w, h, rotation, z, data JSON)`.
  `kind` is `stroke`, `text`, `sticky`, `shape`, `connector`, `image`, `pdf_page` or `link`. A `link` points at a note or
  a card by id. A connector stores the two item ids it joins, so it follows them when they move.

Search is a cosine comparison in numpy over the stored vectors, which is fine for a personal library. Files (originals,
images) live on disk beside the database, not in it. Streaks and stats are computed from `reviews` and `attempts`.

## 4. Server API (`/study/*`, same auth and `changed: study` events as the rest)

Subjects: `GET/POST /study/subjects`, `PATCH/DELETE /study/subjects/{id}`.
Sources: `POST /study/sources` (upload), `GET /study/sources`, `GET /study/sources/{id}` (with outputs and status),
`DELETE`. Import runs in the background and reports progress over the event feed.
Notes: `GET/POST /study/notes`, `PUT/DELETE /study/notes/{id}`.
Search: `GET /study/search?q&subject`.
Cards: `GET /study/cards?state=draft`, `POST /study/cards/{id}/approve`, `PATCH`, `GET /study/due`,
`POST /study/cards/{id}/review {grade}`, `GET /study/stats`.
Practice: `POST /study/practice` (start), `POST /study/practice/{id}/answer`, `POST /study/papers` (upload a past paper).
Topics: `GET /study/topics`, `PATCH /study/topics/{id}` (rename, merge).

### Cyph's tools (added to `cyph/core/toolbox.py`)

`study_search(query, subject?)` returns the top passages with source and page. `study_draft_cards(source_or_topic, n)`
adds `draft` cards for approval. `study_due()` and `study_weak_topics(subject?)` summarise. `study_practice(subject,
kind, topic?)` starts a question. A study system prompt (in `prompts.py`) makes him cite sources, ask before dumping an
answer, and use the hint ladder. The chat message carries `mode: "study"` and a `subject` so the right prompt applies.

### Tutoring (the "interactive" part)

Hint ladder: restate the question, then a nudge, then the first step, then the worked solution. He asks one
follow-up question at a time, notes which step the owner got stuck on, logs that as a weak topic, and re-explains in a
different way if the same step fails twice.

### Scheduling

A simple spaced-repetition schedule with three grades (Again, Hard, Got it), in the style of SM-2. The grade buttons
match the existing mockup. A better algorithm can replace it later without changing the API.

## 5. Apps

**Desktop Study page** (per `docs/mockups/study.html`): subject switcher; library and Notes on the left (upload,
import progress); chat in the middle with citation chips that open the source passage; review pane on the right
(tap or space to flip, keys 1 to 3 to grade) and a streak and weak-topics panel. Maths renders with KaTeX and the
mhchem extension.

**Phone Study tab:** a due-today ring in the new edge style with START; full-screen review with thumb-sized grade
buttons; ask Cyph (reusing the chat's photo attach); add a source by photographing a page; subject list.

**iPad (web app):** the same React frontend built as a web app. This is a real piece of work: today the Rust layer owns
all network I/O and the device token, so the web build needs its own transport that talks to the server directly and
keeps the token in the browser. Pairing uses the same link flow.

**Widget and notifications:** the "2×2 Study due" design becomes real: the edge shows reviews done out of due today,
a tap opens straight into review. The Study notification kind sends "12 cards due", test countdowns and weak-topic
nudges.

## 6. Hard parts, stated plainly

1. **Import quality.** PDF to clean notes with formulas is the hardest thing in phase 1. It uses Gemini on the file
   itself. It must be tried on the owner's own real PDFs before the design is trusted.
2. **iPad over Playit.** The server's certificate is self-signed and names only `cyph-core` and `127.0.0.1`, and the
   apps pin its fingerprint. Safari does not pin, so it needs a certificate that names the Playit address, installed
   and trusted on the iPad, and Apple limits how long such a certificate may be valid. Whether this works with Playit
   and the Pencil canvas is **unknown until the phase 0 spike**. Fallback: Tailscale.
3. **Pen notes offline on iOS.** Safari can clear a web app's stored data, so it must be installed to the Home Screen
   and synced often. Conflicts are handled per page, newest edit wins.
4. **Cost and privacy.** Notes, PDFs and photos are sent to Gemini, as chat already does. Embedding a large PDF costs
   money and time, so indexing runs in the background with a cap and a progress bar.
5. **Wrong cards.** Every drafted card links to its source passage and needs approval.

## 7. Phases (each ends with tests passing and a real run)

- **0. Spikes (small, before any build).**
  A: embed one of the owner's real PDFs with the chosen embedding model, check the model id, vector size and the
  quality of the clean notes. B: Safari + Playit + a trusted certificate, and a Pencil drawing smoke test on the iPad.
  C: KaTeX and mhchem rendering in the app and in chat.
- **1. Subjects, notes and import** (first). Subjects, typed notes, PDF import with the NotebookLM-style outputs,
  embeddings and search, `study_search`, citations. Desktop and phone pages.
- **2. Cards and review** (second). Draft and approve, pictures on cards, the schedule, review screens, stats,
  the Study-due widget and notifications.
- **3. Tutoring and practice.** Hint-ladder tutor, worked problems, SBQ and structured questions, essay and
  comprehension feedback, past papers, weak-topic tracking, tests linked to the Plan with revision blocks.
- **4. iPad web app.** Browser transport, install to Home Screen, trusted HTTPS.
- **5. Pen notes.** One ink engine (pressure, tilt, palm rejection, eraser, lasso) used by two things:
  **5a Notebooks** (pages, backgrounds, shapes and ruler, photos, PDF export) and **5b Freeform boards** (endless pan
  and zoom, text boxes and stickies, shapes and connectors, photos and PDF pages, links to notes and cards, export of
  the whole board or a selected area). Offline save and sync for both. Which of 5a and 5b comes first is open.

## 8. Open items (not decided, not built)

1. **Can Cyph read pen notes?** Handwriting recognition was not chosen. Default: pen pages are private to the owner and
   Cyph sees them only if the owner sends one to him.
2. **Writing cards by hand.** Not chosen. Default: drafts can be edited, but there is no blank "new card" form yet.
3. **Past papers.** Where do mark schemes come from (uploaded with the paper, or Cyph's own judgement), and how does
   the owner answer (typed, or a photo of handwritten work)? Default: a photo or typed text, marked against an
   uploaded scheme when there is one.
4. **Material rules.** Default: only the owner's own notes, school material and papers go in. Cyph does not fetch
   syllabus or exam content from the web.
5. **Spending cap.** A monthly Gemini budget for imports and tutoring.
6. **Notebooks or boards first** when phase 5 starts.
7. **Boards at scale.** An endless board with a lot of ink must stay smooth on the iPad. Drawing the visible area only
   and a size limit per board are the likely answers, to be checked in the phase 0 Pencil test.

## 9. Parked (asked about, not chosen)

Reading handwriting, drawing over PDFs, live interactive visuals, answer-by-doing cards, games and rewards, a native
iPad app, the Architect mode.
