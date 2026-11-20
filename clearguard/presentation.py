"""Light desktop palette shared by the main window and confirmation dialog."""
from tkinter import ttk

BACKGROUND = '#ffffff'
PANEL = '#ffffff'
SURFACE = '#f2f5f9'
BORDER = '#d9e1eb'
TEXT = '#172435'
MUTED = '#53657a'
ACCENT = '#1c5eac'
SELECTION = '#dcecff'


def configure_theme(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(bg=BACKGROUND)
    style.configure('.', background=BACKGROUND, foreground=TEXT, font='TkDefaultFont')
    style.configure('Title.TLabel', font=('TkDefaultFont', 20, 'bold'))
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Summary.TLabel', foreground=TEXT, font=('TkDefaultFont', 12, 'bold'))
    style.configure('TButton', background=SURFACE, foreground=TEXT, bordercolor=BORDER,
                    darkcolor=SURFACE, lightcolor=SURFACE, padding=(10, 6))
    style.map('TButton', background=[('disabled', '#f7f8fa'), ('active', '#e5edf8')],
              foreground=[('disabled', '#737f90')], bordercolor=[('focus', ACCENT)])
    style.configure('Primary.TButton', background=ACCENT, foreground='#ffffff', padding=(15, 6),
                    lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=ACCENT)
    style.map('Primary.TButton', background=[('disabled', '#eaf0f7'), ('active', '#174e90')],
              foreground=[('disabled', '#68788a')], bordercolor=[('focus', '#0f3f73')])
    style.configure('TCheckbutton', indicatorbackground=BACKGROUND, indicatorforeground=ACCENT)
    style.configure('TEntry', fieldbackground=BACKGROUND, foreground=TEXT, insertcolor=TEXT,
                    bordercolor=BORDER, lightcolor=BACKGROUND, darkcolor=BACKGROUND, padding=5)
    style.map('TEntry', bordercolor=[('focus', ACCENT)])
    style.configure('TNotebook', background=BACKGROUND, bordercolor=BORDER,
                    lightcolor=BACKGROUND, darkcolor=BACKGROUND, borderwidth=0, tabmargins=(0, 0, 0, 4))
    style.configure('TNotebook.Tab', background=BACKGROUND, foreground=MUTED, bordercolor=BACKGROUND,
                    lightcolor=BACKGROUND, darkcolor=BACKGROUND, padding=(14, 7), focuscolor=ACCENT)
    style.map('TNotebook.Tab', background=[('selected', '#e8f1fd'), ('active', SURFACE)],
              foreground=[('selected', ACCENT), ('active', TEXT)])
    style.configure('Results.Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, borderwidth=1, rowheight=26)
    style.map('Results.Treeview', background=[('selected', SELECTION)], foreground=[('selected', TEXT)])
    style.configure('Results.Treeview.Heading', background=SURFACE, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, padding=(8, 5))
    style.map('Results.Treeview.Heading', background=[('active', SURFACE)])
    style.configure('TScrollbar', background='#c4d0df', troughcolor='#f1f5f9', bordercolor=BACKGROUND,
                    arrowcolor=MUTED, lightcolor='#c4d0df', darkcolor='#c4d0df', arrowsize=10)
    style.configure('Scan.Horizontal.TProgressbar', background=ACCENT, troughcolor='#e9eff7',
                    bordercolor='#e9eff7', lightcolor=ACCENT, darkcolor=ACCENT, thickness=5)
