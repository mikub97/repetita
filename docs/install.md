# Installing it

You need three things on your computer: **repetita**, a **Python** for it to run
on, and **git** to get updates. None of them needs administrator rights, and
none of them touches the rest of your machine — everything lands in your own
user folder and deleting one folder removes all of it.

Pick your computer.

=== "Windows"

    **1. Install `uv`.** It fetches Python for you, so this is the only install.

    Press <kbd>⊞ Win</kbd>, type `powershell`, press <kbd>Enter</kbd>, then paste
    this line and press <kbd>Enter</kbd>:

    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

    It prints a few lines and finishes in under a minute. **Close the PowerShell
    window and open a new one** — that is how it learns where `uv` went.

    **2. Install git**, from <https://git-scm.com/download/win>. Accept every
    default. It does not ask for an administrator password if you choose
    *"Install for me only"* when offered.

    **3. Get repetita and start it.** Back in PowerShell, one line at a time:

    ```powershell
    cd $HOME\Documents
    git clone https://github.com/mikub97/repetita
    cd repetita
    uv sync
    uv run repetita import courses/ --all --yes
    uv run repetita serve --open
    ```

    The `import` line is the slow one — a few minutes if you took the English
    course, which has 2415 exercises. It only happens once.

    The browser opens by itself. **Leave the PowerShell window open** while you
    study: closing it stops the app.

=== "macOS"

    **1. Install `uv`.** It fetches Python for you, so this is the only install.

    Open **Terminal** (<kbd>⌘</kbd> <kbd>Space</kbd>, type `terminal`), then
    paste this and press <kbd>Return</kbd>:

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    **Close Terminal and open it again** — that is how it learns where `uv` went.

    **2. Get repetita and start it.** git is already on a Mac.

    ```bash
    cd ~/Documents
    git clone https://github.com/mikub97/repetita
    cd repetita
    uv sync
    uv run repetita import courses/ --all --yes
    uv run repetita serve --open
    ```

    The `import` line is the slow one — a few minutes if you took the English
    course, which has 2415 exercises. It only happens once.

    The browser opens by itself. **Leave Terminal open** while you study:
    closing it stops the app.

## Starting it again tomorrow

The same last line, from the same folder:

=== "Windows"

    ```powershell
    cd $HOME\Documents\repetita
    uv run repetita serve --open
    ```

=== "macOS"

    ```bash
    cd ~/Documents/repetita
    uv run repetita serve --open
    ```

Nothing is downloaded and nothing is imported. It opens where you left off, on
the course you were last studying.

## Getting new material

When new exercises are written, they arrive with `git pull` — and then have to
be *imported*, because since [ADR-0015](architecture/decisions/0015-yaml-is-a-way-in-and-a-way-out.md)
repetita never reads course files unless you ask it to:

```bash
git pull
uv run repetita import courses/ --all --yes
```

The import says what it adds, what it changes, and — by name — anything it would
archive. Nothing you wrote yourself is touched: if you edited an exercise here
and it also changed in the files, **your version is kept** and the id is
reported.

## If something goes wrong

**"uv is not recognized" / "command not found: uv"** — the window was open
before `uv` was installed. Close it, open a new one, try again.

**"Address already in use"** — it is already running in another window. Open
<http://127.0.0.1:5116> and carry on, or add `--port 5117`.

**The browser did not open** — go to <http://127.0.0.1:5116> yourself. The
address is printed in the window too.

**You want to start over.** Your study history lives in `data/` inside the
repetita folder and nothing else in the folder is yours. Delete `data/`, run the
`import` line again, and you have a clean start with the same material — but
every schedule and every answer you ever gave goes with it, and nothing can
bring those back. There is a gentler way: `uv run repetita snapshot --list`
shows the copies repetita takes before anything risky, and
`uv run repetita restore <name>` puts one back.

## What got installed, and how to remove it

| what | where | removing it |
| --- | --- | --- |
| `uv` and the Python it fetched | `~/.local/` (macOS) or `%USERPROFILE%\.local\` (Windows) | delete the folder |
| repetita, the courses, your history | the `repetita` folder you cloned | delete the folder |
| git | its own installer (Windows only) | Add or remove programs |

No system Python is changed, nothing is added to a system folder, and no service
runs in the background.
