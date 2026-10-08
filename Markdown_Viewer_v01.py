import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import re
import os


class MarkdownViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("Markdown Viewer")
        self.root.geometry("1000x700")
        self.root.minsize(600, 400)

        self._setup_styles()
        self._build_ui()
        self._bind_events()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Toolbar.TFrame", background="#2d2d2d")
        style.configure("Toolbar.TButton",
                        background="#3c3c3c", foreground="#ffffff",
                        borderwidth=0, focuscolor="none", padding=(10, 5))
        style.map("Toolbar.TButton",
                  background=[("active", "#505050")])

        self.colors = {
            "bg": "#1e1e1e",
            "text": "#d4d4d4",
            "heading1": "#569cd6",
            "heading2": "#4ec9b0",
            "heading3": "#9cdcfe",
            "code_bg": "#2d2d2d",
            "code_fg": "#ce9178",
            "link": "#6a9955",
            "bold": "#dcdcaa",
            "italic": "#c586c0",
            "blockquote": "#808080",
            "hr": "#444444",
            "list_marker": "#569cd6",
        }

    def _build_ui(self):
        # Toolbar
        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame")
        toolbar.pack(fill=tk.X, side=tk.TOP)

        open_btn = ttk.Button(toolbar, text="Datei öffnen",
                              style="Toolbar.TButton", command=self.open_file)
        open_btn.pack(side=tk.LEFT, padx=2, pady=2)

        self.filepath_var = tk.StringVar(value="Keine Datei geladen")
        path_label = tk.Label(toolbar, textvariable=self.filepath_var,
                              bg="#2d2d2d", fg="#888888",
                              font=("Segoe UI", 9), anchor="w")
        path_label.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)

        # Main area with scrollbar
        frame = tk.Frame(self.root, bg=self.colors["bg"])
        frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.text = tk.Text(
            frame,
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI", 12),
            wrap=tk.WORD,
            padx=40,
            pady=20,
            spacing1=2,
            spacing3=4,
            cursor="arrow",
            state=tk.DISABLED,
            yscrollcommand=scrollbar.set,
            relief=tk.FLAT,
            selectbackground="#264f78",
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text.yview)

        self._configure_tags()

    def _configure_tags(self):
        t = self.text
        c = self.colors

        t.tag_configure("h1", font=("Segoe UI", 26, "bold"),
                        foreground=c["heading1"], spacing1=12, spacing3=8)
        t.tag_configure("h2", font=("Segoe UI", 22, "bold"),
                        foreground=c["heading2"], spacing1=10, spacing3=6)
        t.tag_configure("h3", font=("Segoe UI", 18, "bold"),
                        foreground=c["heading3"], spacing1=8, spacing3=4)
        t.tag_configure("h4", font=("Segoe UI", 15, "bold"),
                        foreground=c["heading3"], spacing1=6, spacing3=4)
        t.tag_configure("bold", font=("Segoe UI", 12, "bold"),
                        foreground=c["bold"])
        t.tag_configure("italic", font=("Segoe UI", 12, "italic"),
                        foreground=c["italic"])
        t.tag_configure("bold_italic", font=("Segoe UI", 12, "bold italic"),
                        foreground=c["bold"])
        t.tag_configure("code_inline",
                        font=("Consolas", 11),
                        foreground=c["code_fg"],
                        background=c["code_bg"])
        t.tag_configure("code_block",
                        font=("Consolas", 11),
                        foreground=c["code_fg"],
                        background=c["code_bg"],
                        lmargin1=40, lmargin2=40,
                        spacing1=6, spacing3=6)
        t.tag_configure("blockquote",
                        foreground=c["blockquote"],
                        font=("Segoe UI", 12, "italic"),
                        lmargin1=30, lmargin2=30)
        t.tag_configure("list_item", lmargin1=20, lmargin2=36)
        t.tag_configure("list_marker",
                        foreground=c["list_marker"],
                        font=("Segoe UI", 12, "bold"))
        t.tag_configure("hr", foreground=c["hr"])
        t.tag_configure("link", foreground=c["link"],
                        underline=True)
        t.tag_configure("normal", font=("Segoe UI", 12),
                        foreground=c["text"])

    def _bind_events(self):
        self.root.bind("<Control-o>", lambda _e: self.open_file())

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Markdown-Datei öffnen",
            filetypes=[("Markdown", "*.md *.markdown *.txt"), ("Alle Dateien", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.filepath_var.set(path)
            self.root.title(f"Markdown Viewer — {os.path.basename(path)}")
            self._render(content)
        except Exception as e:
            messagebox.showerror("Fehler", f"Datei konnte nicht geladen werden:\n{e}")

    def _render(self, markdown_text):
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)

        lines = markdown_text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # Fenced code block
            if line.strip().startswith("```"):
                i += 1
                code_lines = []
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                code = "\n".join(code_lines)
                self.text.insert(tk.END, code + "\n", "code_block")
                i += 1
                continue

            # Headings
            heading_match = re.match(r'^(#{1,4})\s+(.*)', line)
            if heading_match:
                level = len(heading_match.group(1))
                text = heading_match.group(2)
                tag = f"h{level}"
                self._insert_inline(text + "\n", tag)
                i += 1
                continue

            # Horizontal rule
            if re.match(r'^(\*{3,}|-{3,}|_{3,})\s*$', line):
                self.text.insert(tk.END, "─" * 60 + "\n", "hr")
                i += 1
                continue

            # Blockquote
            if line.startswith("> "):
                self.text.insert(tk.END, line[2:] + "\n", "blockquote")
                i += 1
                continue

            # Unordered list
            ul_match = re.match(r'^(\s*)([-*+])\s+(.*)', line)
            if ul_match:
                indent = len(ul_match.group(1)) // 2
                content = ul_match.group(3)
                bullet = "  " * indent + "• "
                self.text.insert(tk.END, bullet, ("list_marker", "list_item"))
                self._insert_inline(content + "\n", "list_item")
                i += 1
                continue

            # Ordered list
            ol_match = re.match(r'^(\s*)(\d+)\.\s+(.*)', line)
            if ol_match:
                indent = len(ol_match.group(1)) // 2
                num = ol_match.group(2)
                content = ol_match.group(3)
                marker = "  " * indent + f"{num}. "
                self.text.insert(tk.END, marker, ("list_marker", "list_item"))
                self._insert_inline(content + "\n", "list_item")
                i += 1
                continue

            # Empty line
            if line.strip() == "":
                self.text.insert(tk.END, "\n")
                i += 1
                continue

            # Normal paragraph line
            self._insert_inline(line + "\n", "normal")
            i += 1

        self.text.config(state=tk.DISABLED)

    def _insert_inline(self, text, base_tag):
        """Parse inline elements: bold, italic, inline code, links."""
        # Pattern order matters
        pattern = re.compile(
            r'(`[^`]+`)'                       # inline code
            r'|(\*\*\*[^*]+\*\*\*)'           # bold+italic
            r'|(\*\*[^*]+\*\*|__[^_]+__)'     # bold
            r'|(\*[^*]+\*|_[^_]+_)'           # italic
            r'|(\[([^\]]+)\]\([^)]+\))'       # link [text](url)
        )

        pos = 0
        for m in pattern.finditer(text):
            # Insert plain text before match
            if m.start() > pos:
                self.text.insert(tk.END, text[pos:m.start()], base_tag)

            raw = m.group(0)
            if m.group(1):  # inline code
                inner = raw[1:-1]
                self.text.insert(tk.END, inner, ("code_inline", base_tag))
            elif m.group(2):  # bold+italic
                inner = raw[3:-3]
                self.text.insert(tk.END, inner, ("bold_italic", base_tag))
            elif m.group(3):  # bold
                inner = raw[2:-2]
                self.text.insert(tk.END, inner, ("bold", base_tag))
            elif m.group(4):  # italic
                inner = raw[1:-1]
                self.text.insert(tk.END, inner, ("italic", base_tag))
            elif m.group(5):  # link
                link_text = m.group(6)
                self.text.insert(tk.END, link_text, ("link", base_tag))

            pos = m.end()

        # Remaining plain text
        if pos < len(text):
            self.text.insert(tk.END, text[pos:], base_tag)


def main():
    root = tk.Tk()
    app = MarkdownViewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
