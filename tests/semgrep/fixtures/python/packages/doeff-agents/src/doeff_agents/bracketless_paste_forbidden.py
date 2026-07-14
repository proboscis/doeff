"""Hit fixture: tmux paste-buffer without -p (bracketed paste) is banned."""


def paste_sample(subprocess, args_fn, buffer_name, target):
    subprocess.run(args_fn("paste-buffer", "-b", buffer_name, "-t", target), check=True)
