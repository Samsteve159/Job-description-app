/* The app's real executable.
 *
 * This existed as a shell script and could never hold a permission. macOS assigns file
 * access to the process it actually launches, and for a script that is /bin/bash, not the
 * app. Full Disk Access granted to Job App landed on nothing and the app was refused every
 * time, while the person granting it had done everything correctly.
 *
 * A Mach-O binary is its own identity. It becomes the responsible process, and the shell
 * it spawns inherits that, so the grant finally applies to the thing doing the reading.
 *
 * Built by desktop/build_app.sh. Nothing here is app logic; run.sh beside it holds that.
 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <libgen.h>
#include <string.h>
#include <limits.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    char self[PATH_MAX];
    uint32_t size = sizeof(self);
    if (_NSGetExecutablePath(self, &size) != 0) return 1;

    char resolved[PATH_MAX];
    if (realpath(self, resolved) == NULL) return 1;

    /* .../Job App.app/Contents/MacOS/JobApp -> .../Contents/Resources/run.sh */
    char *macos = dirname(resolved);          /* .../Contents/MacOS   */
    char contents[PATH_MAX];
    snprintf(contents, sizeof(contents), "%s", dirname(macos));

    char script[PATH_MAX];
    snprintf(script, sizeof(script), "%s/Resources/run.sh", contents);

    execl("/bin/bash", "bash", script, (char *)NULL);
    return 127;
}
