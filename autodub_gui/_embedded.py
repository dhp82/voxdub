"""Giá trị nhúng cứng vào bản đóng gói (exe).

File này trong repo LUÔN rỗng. Khi build exe, ``scripts/build_exe.py`` sinh
lại nó với REMOTE_CONTROL_URL đọc từ .env của máy build, rồi khôi phục về
rỗng sau khi build xong — URL kill-switch nằm TRONG exe, không lộ ra .env
của người dùng và người dùng không chỉnh được.
"""

# Rỗng = không nhúng; remote_gate rơi về biến môi trường (chế độ dev).
REMOTE_CONTROL_URL = ''
