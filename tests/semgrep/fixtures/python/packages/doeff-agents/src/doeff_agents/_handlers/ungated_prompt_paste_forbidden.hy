;;; Hit fixture: Hy handlers must not paste the launch prompt ungated.

(when prompt
  (.send-keys active-backend session-info.pane-id prompt))
