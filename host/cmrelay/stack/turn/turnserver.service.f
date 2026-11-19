[Unit]
Description=A wrapper for the TURN server
After=network.target

[Service]
Type=simple
Restart=always
# chatmail-turn binds every address and exits when one bind fails, as it does on an IPv6
# address that duplicate address detection still holds tentative, for a second or two after
# it appears; the restarts then hit the start limit and leave the unit failed. Waits 5s at most.
ExecStartPre=/bin/sh -c 'for i in $$(seq 50); do [ -z "$$(ip -6 addr show tentative)" ] && exit 0; sleep 0.1; done'
ExecStart={bin_path} --realm {mail_domain} --socket /run/chatmail-turn/turn.socket

# Create /run/chatmail-turn
RuntimeDirectory=chatmail-turn
User=vmail
Group=vmail

[Install]
WantedBy=multi-user.target
