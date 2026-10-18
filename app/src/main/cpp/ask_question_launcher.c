#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>

int main(int argc, char *argv[]) {
    char script_path[512] = {0};
    const char *prefix = getenv("PREFIX");
    if (prefix && prefix[0] != '\0') {
        snprintf(script_path, sizeof(script_path), "%s/share/agy-ui/ask_question.py", prefix);
    }
    if (script_path[0] == '\0' || access(script_path, R_OK) != 0) {
        // App internal ext4 asset path
        const char *cand1 = "/data/data/ai.agy.app/files/usr/share/agy-ui/ask_question.py";
        const char *cand2 = "/data/data/com.termux/files/usr/share/agy-ui/ask_question.py";
        if (access(cand1, R_OK) == 0) {
            strncpy(script_path, cand1, sizeof(script_path) - 1);
        } else if (access(cand2, R_OK) == 0) {
            strncpy(script_path, cand2, sizeof(script_path) - 1);
        } else {
            strncpy(script_path, cand1, sizeof(script_path) - 1);
        }
    }

    char **new_argv = malloc((argc + 2) * sizeof(char *));
    if (!new_argv) {
        perror("malloc");
        return 1;
    }
    new_argv[0] = "python3";
    new_argv[1] = script_path;
    for (int i = 1; i < argc; i++) {
        new_argv[i + 1] = argv[i];
    }
    new_argv[argc + 1] = NULL;

    // Execute through python3 interpreter (avoids Android SELinux W^X shebang blockage)
    execvp("python3", new_argv);
    execvp("/data/data/ai.agy.app/files/usr/bin/python3", new_argv);
    execvp("/data/data/com.termux/files/usr/bin/python3", new_argv);
    perror("execvp python3 failed");
    free(new_argv);
    return 1;
}
