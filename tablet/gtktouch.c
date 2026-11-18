/*
 * libtablet-gtktouch.so - a GTK 3 module for the tablet's Linux: in GTK's file dialogs
 * (Open, Save, choose folder) one tap opens a folder or picks a file. GTK
 * itself has no setting for that; its file list waits for a double click,
 * which a finger rarely produces. Loaded through GTK_MODULES (tablet-session).
 *
 * How: when a GtkTreeView is mapped inside a GtkFileChooserWidget, it gets
 * gtk_tree_view_set_activate_on_single_click(TRUE).
 *
 * Every GLib/GTK function is looked up at run time (dlsym), so the module
 * needs no GTK headers to build, and in a GTK 2 program (which loads
 * GTK_MODULES too) it does nothing instead of failing on a missing symbol.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stddef.h>

typedef unsigned long GType;      /* gsize */
typedef int gboolean;
typedef struct _GValue GValue;     /* only passed through */
typedef struct _GSignalInvocationHint GSignalInvocationHint;
typedef gboolean (*GSignalEmissionHook)(GSignalInvocationHint *hint, unsigned n_params,
                                        const GValue *params, void *data);

static struct {
    GType (*tree_view_type)(void);
    GType (*chooser_type)(void);
    void *(*class_ref)(GType type);
    unsigned (*signal_lookup)(const char *name, GType type);
    unsigned long (*add_hook)(unsigned signal_id, unsigned detail, GSignalEmissionHook hook,
                              void *data, void (*destroy)(void *));
    void *(*value_get_object)(const GValue *value);
    gboolean (*is_a)(void *instance, GType type);
    void *(*get_ancestor)(void *widget, GType type);
    void (*set_single_click)(void *tree_view, gboolean single);
    GType tree_view, chooser;
} g;

static gboolean on_map(GSignalInvocationHint *hint, unsigned n_params, const GValue *params,
                       void *data)
{
    void *widget;

    (void)hint;
    (void)data;
    if (n_params < 1)
        return 1;
    widget = g.value_get_object(params);
    if (widget && g.is_a(widget, g.tree_view) && g.get_ancestor(widget, g.chooser))
        g.set_single_click(widget, 1);
    return 1;                         /* keep the hook */
}

#define FIND(field, name) (*(void **)&g.field = dlsym(RTLD_DEFAULT, name))

void gtk_module_init(int *argc, char ***argv)
{
    unsigned (*major)(void) = NULL;
    unsigned map;

    (void)argc;
    (void)argv;
    *(void **)&major = dlsym(RTLD_DEFAULT, "gtk_get_major_version");
    if (!major || major() != 3)
        return;
    if (!FIND(tree_view_type, "gtk_tree_view_get_type") ||
        !FIND(chooser_type, "gtk_file_chooser_widget_get_type") ||
        !FIND(class_ref, "g_type_class_ref") || !FIND(signal_lookup, "g_signal_lookup") ||
        !FIND(add_hook, "g_signal_add_emission_hook") ||
        !FIND(value_get_object, "g_value_get_object") ||
        !FIND(is_a, "g_type_check_instance_is_a") ||
        !FIND(get_ancestor, "gtk_widget_get_ancestor") ||
        !FIND(set_single_click, "gtk_tree_view_set_activate_on_single_click"))
        return;
    g.tree_view = g.tree_view_type();
    g.chooser = g.chooser_type();
    g.class_ref(g.tree_view);         /* signals exist once the class is set up */
    map = g.signal_lookup("map", g.tree_view);
    if (map)
        g.add_hook(map, 0, on_map, NULL, NULL);
}
