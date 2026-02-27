import time
import vgamepad as vg

pads = [vg.VX360Gamepad() for _ in range(4)]

# Carregar um botão diferente em cada comando
btns = [
    vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
]

print("A carregar A/B/X/Y nos 4 comandos durante 3 segundos...")
for i, p in enumerate(pads):
    p.press_button(btns[i])
    p.update()

time.sleep(3)

for p in pads:
    p.reset()
    p.update()

print("Feito. Agora abre um programa que leia XInput (PCSX2/Steam/DS4Windows) e vê se aparecem 4.")