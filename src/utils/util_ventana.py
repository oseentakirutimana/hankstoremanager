def centrar_ventana(win, width, height):
    """
    Centre une fenêtre Tk ou Toplevel. Ignore si ce n’est pas une fenêtre.
    """
    if not hasattr(win, "geometry"):
        return
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 2
    win.geometry(f"{width}x{height}+{x}+{y}")
