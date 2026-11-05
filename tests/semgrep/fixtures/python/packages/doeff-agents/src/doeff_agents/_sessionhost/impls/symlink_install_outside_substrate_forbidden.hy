;; Positive control (3 lines fire) -- spellings that reassemble the physics of installation outside the single point.
(os.symlink target link)
(.symlink_to (Path link) target)
(subprocess.run f"ln -sfn {target} {link}" :shell True)
;; Discrimination (does not fire) -- shapes that delegate to the single point, read-only syscalls, and the symlink-swap verb itself.
(fs-ensure-symlink link target)
(os.readlink link)
(ensure-symlink-outcome link target)
