(import socket)


(defn host-name []
  (socket.gethostname))
