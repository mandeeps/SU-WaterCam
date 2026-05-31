# UFONet WaterCam – Component Keep-Out Reference

**Rev 0.3 | UFO-Net / WaterCam Sensor Stack**

This document covers mechanical dimensions, keep-out zones, special placement constraints, and conformal coating instructions for each component in the WaterCam sensor assembly. Dimensions are in millimeters unless noted. All dimensions should be verified against the linked official sources before committing to a PCB layout.

> **Conformal Coating Note:** Two coating materials are in use across WaterCam builds. Third-party manufactured units use **acrylic** (IPC-CC-830 Type AR, 25–75 µm). Student-assembled units may use **MG brand modified silicone** (IPC-CC-830 Type SR). Keep-out zones are identical for both materials; process procedure and safety considerations differ — see [Section 0](#0-conformal-coating-material-selection) before beginning.

---

## 0. Conformal Coating Material Selection

### Is acrylic sufficient for WaterCam?

**Yes.** WaterCam units are housed in IP65-rated enclosures. The conformal coating is a secondary protection layer against condensation forming inside the enclosure — not a substitute for the enclosure seal. Acrylic (IPC-CC-830 Type AR) at 25–75 µm is standard practice for this type of deployment and is the correct specification for third-party manufacturing.

Acrylic advantages for this application:
- Simple, consistent application by spray or brush
- Tack-free in ~30 min, full cure in 24 h
- Reworkable with acetone or MEK — failed components can be replaced in the field
- No outgassing after cure — safe to close enclosure immediately

### MG Modified Silicone — when and why

MG brand modified silicone (e.g., MG 422B) is a silicone-acrylic hybrid (IPC-CC-830 Type SR). It offers better flexibility across wider temperature swings (−65°C to +200°C vs. acrylic's typical −65°C to +125°C) and greater moisture resistance if the IP65 seal is compromised.

Students may use MG modified silicone for units they assemble. The keep-out masking zones are identical to acrylic. However, silicone introduces three risks specific to this hardware that require additional procedure steps described below.

### Material Comparison

| Property | Acrylic (third-party) | MG Modified Silicone (student) |
|---|---|---|
| IPC-CC-830 type | AR | SR |
| Thickness target | 25–75 µm | 50–130 µm (2 thin coats) |
| Tack-free time | ~30 min | ~30 min |
| Functional cure | 24 h | 24 h |
| Off-gas period before enclosure close | None required | **72 h minimum** (see AHT20 risk below) |
| Rework solvent | Acetone or MEK | MG 8340 thinner or mechanical removal only |
| Viscosity | Low — good spray atomisation | Higher — apply thin coats, avoid flood |
| Masking tape bond risk | Low | Moderate — use low-tack tape (3M ScotchBlue 2090) |

### Silicone-Specific Risk 1 — AHT20 Humidity Sensor Contamination (CRITICAL)

Silicone coatings off-gas volatile cyclic siloxanes (D4, D5) during and after cure. These vapors deposit on the AHT20's capacitive polymer sensing film, filling the hygroscopic pores and reducing effective surface area. The result is a persistent, systematic **low-humidity offset** that may not recover fully at ambient conditions. This is a documented failure mode for polymer-film humidity sensors exposed to silicone coating outgassing.

**Mitigation for silicone builds:**
- After coating and functional cure (24 h), leave the board in open air for a minimum of **72 h** before installing into the enclosure.
- Do not close the enclosure until off-gassing is complete — siloxanes trapped inside will re-deposit on the sensor.
- After 72 h open-air off-gas, perform a reference humidity check by comparing the AHT20 reading against a calibrated reference (a second uncoated sensor or a reference hygrometer). If offset exceeds ±4% RH, extend off-gas period or replace the AHT20.
- Alternatively: install the AHT20 **after** coating and off-gassing are complete. The AHT20's DFN-6 package is a simple hand-solder part (6 pads, 3 mm × 3 mm); installing it last avoids exposure entirely.

### Silicone-Specific Risk 2 — Optical Surface Contamination

Siloxane vapor deposits a thin, partially absorptive film on optical surfaces:

- **Dorhea camera lens:** Silicone film causes visible haze in RGB images. Even minor contamination reduces contrast at the water surface edge used for flood-line detection.
- **Lepton lens (internal) and PE IR window (enclosure):** Silicone film deposits on the Lepton's internal lens element reduce thermal sensitivity (higher effective NETD). The enclosure PE window (EO #2593, 0.38 mm polyethylene) is non-polar — siloxane physisorption on PE is relatively low compared to polar or crystalline surfaces, so the window itself is a secondary concern. The primary reason not to close the enclosure during off-gassing is the AHT20. That said, if siloxane does deposit on the PE window, clean with IPA; unlike crystalline windows, PE is not damaged by IPA.

**Mitigation for silicone builds:**
- Remove Lepton module before coating (already required for all builds).
- Remove Dorhea camera from its mount and store in a sealed anti-static bag before coating.
- Do **not** reinstall either optical component until the full 72 h off-gas period is complete and the board is installed in the enclosure with the enclosure briefly vented.

### Silicone-Specific Risk 3 — Connector Contact Contamination

Silicone that contacts metal spring fingers (Molex socket, U.FL connectors, GPIO pins) cannot be removed with acetone or IPA. It requires MG 8340 thinner and mechanical scrubbing, or micro-abrasive blasting — neither practical for field repair.

**Mitigation for silicone builds:**
- For the Molex 32-pin Lepton socket: tape is not sufficient. Insert a physical foam plug cut to the socket cavity dimensions, then tape over it. Remove foam plug after curing.
- For U.FL connectors: use a mated dummy connector or wrap with self-fusing silicone tape (yes — silicone tape as mask, because it does not bond to cured silicone coating the way acrylic-based tape adhesive does).
- For GPIO headers: wrap entire header with self-fusing silicone tape rather than electrical tape.
- Inspect all contacts under magnification before reinstalling any connector. Any silicone residue on spring fingers requires 8340 thinner and a cotton swab before assembly.

### Silicone Application Notes for Students

1. **Clean the board first.** Remove all flux residue with IPA. Allow IPA to evaporate completely (minimum 15 min at room temperature) before applying silicone — residual IPA under curing silicone causes adhesion failure and blistering.
2. **Apply in two thin coats, not one thick coat.** MG modified silicone is more viscous than acrylic. A single thick coat runs and drips around connector edges. Let first coat become tack-free (~30 min) before applying second coat.
3. **Do not spray at close range.** If using aerosol, hold the can at 30 cm minimum. Close-range silicone spray globs rather than atomising.
4. **Feather brush strokes away from masked areas.** Load the brush lightly and stroke outward from the masked edges — do not stroke toward them. Silicone wicks more aggressively than acrylic.
5. **Use low-tack masking tape.** Standard painter's tape or electrical tape may bond to the silicone surface on removal, leaving adhesive residue on PCB pads. 3M ScotchBlue 2090 is a suitable choice. Peel masking within 1 hour of tack-free (do not wait for full cure to remove masking — it becomes harder to peel cleanly).
6. **Document what you coated.** Photograph the board before and after coating. Record which board serial number received silicone. Rework requires knowing which material was used.

### Revised Process Order — Silicone Builds

1. Clean board with IPA. Wait 15 min for full evaporation.
2. **Remove** FLIR Lepton module. Store in sealed anti-static bag.
3. **Remove** Dorhea camera. Store in sealed bag.
4. Apply physical foam plug to Molex socket. Apply self-fusing silicone tape to GPIO headers and U.FL connectors. Apply low-tack tape (3M 2090) to all other MASK zones.
5. Apply first coat of MG modified silicone. Allow 30 min tack-free.
6. Apply second coat. Allow 24 h functional cure at room temperature.
7. Peel all masking (do not wait beyond 1 h after tack-free for tape sections).
8. Inspect contacts under magnification. Clean any silicone residue from spring fingers with MG 8340.
9. **Leave board in open air for 72 h** to off-gas siloxanes. Do not place in enclosure during this period.
10. After 72 h: perform AHT20 reference humidity check (compare against calibrated reference). If offset >±4% RH, extend off-gas period or replace AHT20.
11. Inspect optical paths: confirm no haze on Dorhea lens, confirm no film visible on Lepton lens. Do not install enclosure polymer IR window until off-gas is complete.
12. Reinstall Dorhea camera. Reinstall FLIR Lepton module into Breakout Board socket.
13. Install board into enclosure.

---

## 1. Raspberry Pi 4 Model B

| Attribute | Value |
|---|---|
| PCB | 85.0 × 56.0 mm |
| Thickness | 1.4 mm nominal |
| Mounting holes | 4× M2.5, located at corners (see drawing) |
| GPIO header | 40-pin 2×20, 2.54 mm pitch |
| Tallest component (USB-A) | ~15.0 mm above top of PCB |
| RJ45 height | ~13.5 mm |
| USB-C power | Short edge, extends ~3.2 mm beyond board edge |
| MicroSD protrusion | ~2.8 mm beyond short edge |
| Camera FPC (CSI-2) | Top edge, 15-pin 1.0 mm pitch |
| Display FPC (DSI) | Top edge, 15-pin 1.0 mm pitch |

### Keep-Out Zones

**Connector access clearance.** The USB-C power port, dual USB 3.0/2.0 ports, and RJ45 all protrude from the long edges of the board. The USB-C extends 3.2 mm past the PCB edge; provide a minimum 5 mm clearance on the USB-C side to allow cable insertion.

**GPIO header region.** The 40-pin header runs along the long edge. If stacking a HAT, standard M2.5 brass standoffs require 11 mm minimum clearance between board surfaces. Do not route signals under the header pins.

**Under-board clearance.** The underside of the Pi 4B has SMT components. Minimum 1.0 mm clearance below the board to any substrate surface. Do not place any components directly under the Pi footprint on the same carrier PCB if the Pi is soldered down.

**Camera/Display FPC connectors.** Located along the top long edge. Each requires ~5 mm clearance above the PCB for the cable lock arm to open. Do not obstruct the cable path toward the lens or display.

**RF.** The Pi 4B has an onboard Wi-Fi/BT antenna in the top-right corner (opposite end from USB-C). Do not place metallic structures within ~5 mm of the antenna module area on the same vertical plane.

### Conformal Coating — Raspberry Pi 4B

```
Pi 4B Top View (85 mm × 56 mm)
┌──────────────────────────────────────────────────┐
│ [MASK] USB-A × 2    [MASK] RJ45    Antenna ████  │
│ ████████████        ███████████              ▲   │
│                                              │   │
│                    PCB body                  │   │
│                    COAT OK                   │   │
│                                          RF keep-│
│                                          out 5mm │
│ ████ USB-C [MASK]                            │   │
│ ████ MicroSD [MASK]                          │   │
│                                              │   │
│ ○○○○○○○○○○○○○○○○○○○○  GPIO [MASK]           │   │
│ ○○○○○○○○○○○○○○○○○○○○                            │
│ [CSI-2 FPC]  [DSI FPC]  ← both MASK             │
└──────────────────────────────────────────────────┘
```

| Zone | Treatment |
|---|---|
| PCB body (SoC, RAM, passives) | COAT OK |
| USB-A, USB-C, RJ45, HDMI connectors | MASK — connector contacts must remain uncoated |
| MicroSD slot | MASK — card insertion must remain possible |
| GPIO 40-pin header | MASK — WittyPi HAT connection must remain clean |
| CSI-2 FPC connector (camera) | MASK — Dorhea FPC must remain removable |
| DSI FPC connector | MASK — if display is installed; mask regardless |
| Wi-Fi/BT antenna module area | Do not coat; keep metal structures 5 mm clear |

**Official Resources**
- Mechanical drawing (PDF): https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-mechanical-drawing.pdf
- Product Information Portal: https://pip.raspberrypi.com/categories/559-mechanical
- Full datasheet: https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-datasheet.pdf

---

## 2. WittyPi 4 (UUGear Power Management HAT)

| Attribute | Value |
|---|---|
| Form factor | Standard Raspberry Pi HAT |
| PCB | 65.0 × 56.0 mm |
| GPIO connector | 2×20 SMT header, base height 3.5 mm (updated Aug 2025; prior version was 2.0 mm) |
| Input voltage | Up to 30V DC via screw terminal |
| Operating temperature | −30°C to +80°C |

### Keep-Out Zones

**Stack height.** When mounted on the Pi 4B via the 40-pin GPIO header, the WittyPi 4 board surface sits approximately 3.5 mm above the Pi GPIO pin tops. Combined with the Pi's 15 mm tallest component, ensure the enclosure interior has adequate clearance for the stack.

**I2C address space.** The WittyPi 4 acts as an I2C proxy for its onboard RTC and temperature sensor. The I2C address is configurable to avoid conflicts with the BNO085 and AHT20 (0x38). Confirm address assignments before finalizing firmware.

**Power wiring keep-out.** The DC input screw terminal is on one edge of the board. Ensure adequate clearance for the power cable routing within the enclosure, especially near the IP65 cable gland entry point.

### Conformal Coating — WittyPi 4

```
WittyPi 4 Top View (65 mm × 56 mm)
┌────────────────────────────────────┐
│                                    │
│  DC/DC converter   RTC chip        │
│  (tallest ~10 mm)  DS3231SN        │
│  COAT OK           COAT OK         │
│                                    │
│  PCB body / passives               │
│  COAT OK                           │
│                                    │
│  ████ DC screw terminal [MASK] ████│ ← power wires pass through
│                                    │
│ ○○○○○○○○○○○○○○○○○○○○ GPIO header  │
│ ○○○○○○○○○○○○○○○○○○○○ [MASK]       │ ← mates with Pi GPIO
└────────────────────────────────────┘
```

| Zone | Treatment |
|---|---|
| PCB body (DC/DC converter, RTC, passives) | COAT OK |
| 40-pin GPIO header (underside, contacts Pi) | MASK — contacts must remain clean for electrical connection |
| DC input screw terminal | MASK — power wire termination must remain serviceable |

**Official Resources**
- Product page: https://www.uugear.com/product/witty-pi-4/
- User manual: https://www.uugear.com/doc/WittyPi4_UserManual.pdf
- STEP model (Mini variant): https://www.uugear.com/repo/WittyPi4/WittyPi4Mini.step

> Note: UUGear does not appear to publish a standalone mechanical drawing PDF for the WittyPi 4. Dimensions can be extracted from the STEP file or measured from the standard HAT template.

---

## 3. FLIR Lepton 3.5

| Attribute | Value |
|---|---|
| Module (without socket) | 11.5 × 12.7 × 6.9 mm |
| Module (with Molex socket) | 11.8 × 12.7 × 7.2 mm |
| Resolution | 160×120 pixels |
| Spectral band | LWIR 8–14 µm |
| Field of view | 57° horizontal / 71° diagonal (with shutter) |
| Interface | 32-pin Molex socket (105028-1001 or 105028-2031) |
| Max mounting force | 1 kgF (do not exceed) |
| Operating power | ~140 mW typical; ~800 mW during shutter event |
| Operating temperature | −10°C to +80°C |

### Keep-Out Zones

**Optical field of view.** The 57° horizontal × 71° diagonal FoV cone must be completely clear of any enclosure wall, baffle, or coating. The cone extends from the lens face outward; nothing may encroach on this volume.

**Mechanical shutter pins.** The Lepton 3.5 has a motorized internal shutter. The shutter mechanism has protruding actuator pins on the sides of the housing. Any foam isolator, retention clip, or bracket must be cut to avoid contacting these pins. Maximum uniform load on shutter face: 1 kgF.

**Thermal keep-out.** Do not place heat-generating components within the Lepton's FoV. Minimize thermal gradient across the camera body — per the Teledyne FLIR Engineering Datasheet, the surrounding area must support dissipation of up to 160 mW. Thermally isolate the Lepton mounting from the main PCB ground plane where possible.

**Socket contact zone.** The Lepton module is NOT a sealed assembly (per FLIR Engineering Datasheet §6.2). Its 32 side contacts engage the Molex socket through wiping contact. Any liquid (coating, flux residue, condensation) that enters the socket-module interface will cause contact resistance failures. The Lepton module must never be coated.

**Polyethylene IR window.** The WaterCam enclosure uses an Edmund Optics 6″ × 6″ Translucent IR Material Window (EO #2593) cut to fit the **Lepton aperture only**. This window does not cover the Dorhea optical camera — the Dorhea lens faces the environment directly (see Section 5). Material is **polyethylene (PE)**, 0.38 mm thick, molded translucent/milky white sheet. Transmission spans 8–14 µm (LWIR, matching the Lepton 3.5 spectral response) and extends to ~40 µm. The FLIR Engineering Datasheet §6.2 cites germanium and zinc selenide as common crystalline options — those are not in use here.

Key handling notes for this window:
- **Thickness 0.38 mm — very flexible and scratch-prone.** Handle by edges only. Do not lay flat on abrasive surfaces.
- **Solvent compatibility.** PE is resistant to IPA, acetone, and the carrier solvents in both acrylic and silicone conformal coatings. Do not use aromatic solvents (toluene, xylene) or chlorinated solvents near the window — these swell PE. Clean with IPA or mild soap and water only.
- **UV degradation.** Outdoor UV exposure causes PE to yellow and embrittle over time. Inspect the window at each service interval; replace if yellowing or surface crazing is visible, as this scatters LWIR and degrades thermal image uniformity.
- **Silicone outgassing.** PE is non-polar; siloxane physisorption from silicone coating outgassing is lower on PE than on polar or crystalline surfaces. However, the precaution of not closing the enclosure until the 72 h off-gas period is complete still applies — primarily to protect the AHT20, not the window. See Section 0, Silicone Risk 2.
- The window is part of the enclosure, not the PCB assembly, and is not subject to the conformal coating process.
- The window must not impinge on the 57° H / 71° D optical keep-out cone.

### Conformal Coating — FLIR Lepton 3.5 Module

```
FLIR Lepton 3.5 — Side View (with shutter)

        ┌── Germanium lens window ──┐
        │     ╔═══════════╗         │
◄──────►│     ║  LWIR     ║         │   NO COAT on any surface
57° FoV │     ║  sensor   ║         │   NO COAT on lens
        │     ╚═══════════╝         │   NO COAT on shutter pins
        └───────────────────────────┘   NO COAT on 32 side contacts
                 │ │ │ │ │ │ │ │
            ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   ← 32-pin side contacts
            (mate into Molex socket)
```

| Zone | Treatment |
|---|---|
| Germanium lens window | **NO COAT** — any coating blocks LWIR; permanent damage |
| Shutter actuator pins (sides) | **NO COAT, NO OBSTRUCT** — shutter event exerts force; coating can jam mechanism |
| 32-pin side contacts | **NO COAT** — must make wiping contact with socket; coating prevents seating |
| Module housing | **NO COAT** — module must remain removable from socket |

> **The Lepton module as a whole must never enter a coating process.** Remove from socket before board coating. Reinstall after coating is fully cured and masking removed.

**Official Resources**
- Engineering Datasheet Rev 400: Teledyne FLIR Doc. # 500-0771-01-09 (on file at `Docs/instructions/`)
- Mechanical IDD for Lepton 3.5: 500-0771-41 (request from Teledyne FLIR)
- Breakout board schematic: `Docs/instructions/FLIR_Lepton_Breakout_Board_V2_Schematic-16912.pdf`

---

## 4. FLIR Lepton Breakout Board V2.0 (PN: 250-0577-00)

| Attribute | Value |
|---|---|
| PCB | 29.5 × 29.0 mm |
| Total thickness (excl. Lepton) | 15.0 mm (PCB + Molex socket + jumper pins) |
| Lepton socket | 32-pin Molex (105028-1001 compatible) |
| Interface header | J2: 20-pin, 2.54 mm pitch (2×10), 100 mil |
| Input voltage | 3.0 V to 5.5 V |
| Operating temperature | 0°C to 55°C |
| Jumpers | J5–J9 (bypass for voltage/clock/power-up sequence) |

### Keep-Out Zones

**Molex socket.** The 32-pin Molex socket is the mechanical and electrical interface to the Lepton module. The socket has side-contact spring fingers that must make unobstructed wiping contact with the Lepton's pads. No coating, flux, or other material may enter the socket interior or contact the spring fingers.

**Jumpers J5–J9.** These bypass the onboard 1.2 V LDO, 2.8 V LDO, master clock, and power-up sequencer. They are installed by default and configure the board for standard 3–5 V operation. Mask these for coating so they remain reconfigurable if power supply topology changes.

**J2 header (Pi interface).** This 20-pin header connects the breakout board to the Raspberry Pi SPI/I2C/power lines. Mask the header if any chance of future disconnection exists. If permanently wire-soldered, the solder joints can be coated after soldering.

**Mounting holes.** Four 2.0 mm diameter mounting holes at corners (see Fig. 1 in datasheet). Mask holes and surrounding ~2 mm annulus if hardware will be installed after coating; coating inside threaded standoff holes causes poor thread engagement.

### Conformal Coating — Lepton Breakout Board V2.0

```
Lepton Breakout Board V2.0 — Top View (29.5 mm × 29.0 mm)

  J2 (20-pin, 2×10 header) — MASK
  ┌──○─○─○─○─○─○─○─○─○─○──┐
  │  ○─○─○─○─○─○─○─○─○─○  │
  │                        │
  │  ┌──────────────────┐  │
  │  │  32-pin Molex    │  │
  │  │  Lepton socket   │  │
  │  │                  │  │
  │  │  ████ MASK ████  │  │
  │  │  (entire socket  │  │
  │  │   interior and   │  │
  │  │   spring fingers)│  │
  │  └──────────────────┘  │
  │                        │
  │  [J5][J6][J7][J8][J9] ← MASK (jumpers)
  │                        │
  │  Y1  C1–C4  U1  R1–R5 │ ← COAT OK (passives, LDOs, oscillator)
  │                        │
  ○ mounting holes — MASK  ○
  └────────────────────────┘
```

| Zone | Treatment |
|---|---|
| 32-pin Molex socket (entire) | **MASK** — spring fingers must remain unobstructed; coating prevents Lepton seating |
| Jumpers J5–J9 | **MASK** — must remain reconfigurable |
| J2 20-pin header | **MASK** — Pi interface must remain electrically clean |
| Mounting holes (4×) | **MASK** — thread engagement |
| PCB body (LDOs, passives, oscillator Y1, U1) | COAT OK |

> **Lepton module itself:** Remove from socket before coating. Do not coat. Reinstall after curing.

**Official Resources**
- Breakout board datasheet: `Docs/instructions/Lepton Camera Breakout Board-V2.0-Datasheet-US.pdf`
- Schematic: `Docs/instructions/FLIR_Lepton_Breakout_Board_V2_Schematic-16912.pdf`
- Getting started guide: `Docs/instructions/Getting started with the Raspberry Pi and Breakout Board V2.0.pdf`

---

## 5. Dorhea IR-Cut Camera (Optical / NIR)

| Attribute | Value |
|---|---|
| Interface | CSI-2 FPC, 15-pin 1.0 mm pitch (connects to Pi CSI-2 port) |
| Resolution | 2592 × 1944 pixels (~5 MP) |
| IR-cut filter | Motorized, GPIO-controlled (GPIO 21 on Pi) |
| Filter state — LOW | IR filter IN (visible-light-only mode) |
| Filter state — HIGH | IR filter OUT (NIR+visible mode) |
| Filter settle time | ~300 ms mechanical travel; 500 ms software margin |

### Keep-Out Zones

**Lens and image sensor.** The lens assembly and image sensor must be completely clear of coating, condensation, and contamination. Any coating on the lens surface causes permanent image degradation and cannot be cleaned off once cured.

**IR-cut filter motor mechanism.** The motorized IR-cut filter is a precision mechanical assembly. Coating that contacts the filter carriage, motor shaft, or actuator linkage will bind the mechanism. GPIO 21 drives the motor; if the mechanism is seized, the NIR-OFF/NIR-ON capture pair will produce identical images, invalidating the reflectance ratio used for water surface analysis.

**CSI-2 FPC connector.** The 15-pin FPC connects to the Pi's CSI-2 port. Both the connector on the camera board and the lock arm on the Pi must remain free of coating. Keep FPC bend radius above 5 mm; route away from enclosure edges.

**Direct environmental exposure.** Unlike the Lepton, the Dorhea lens has no protective window between it and the scene being filmed. The lens is directly exposed to the environment through the enclosure aperture. Ensure the enclosure aperture is sized and sealed (gasket or O-ring) to prevent rain ingress and condensation from reaching the lens face while preserving the optical field of view. Do not allow standing water to contact the lens — the IR-cut filter motor behind the lens is not rated for immersion.

### Conformal Coating — Dorhea IR-Cut Camera

```
Dorhea IR-Cut Camera — Top View (approximate)

  ┌──────────────────────────────────┐
  │                                  │
  │   ┌──────────────────────────┐   │
  │   │    Lens assembly         │   │
  │   │    + Image sensor        │   │
  │   │                          │   │
  │   │   ████████ MASK ████████ │   │
  │   │   (entire optical path)  │   │
  │   └──────────────────────────┘   │
  │                                  │
  │   IR-cut filter motor            │
  │   ┌──────────────────────┐       │
  │   │ filter carriage/motor│       │
  │   │   ████ MASK ████     │       │
  │   └──────────────────────┘       │
  │                                  │
  │   PCB body                       │
  │   COAT OK (non-optical areas)    │
  │                                  │
  │═══════════════════════════════   │ ← FPC connector
  │   CSI-2 FPC [MASK]               │
  └──────────────────────────────────┘
```

| Zone | Treatment |
|---|---|
| Lens assembly | **MASK** — optical surface; permanent damage if coated |
| Image sensor face | **MASK** — permanent contamination |
| IR-cut filter motor + carriage | **MASK** — mechanical binding; destroys NIR capture capability |
| CSI-2 FPC connector | **MASK** — removable connection to Pi CSI-2 port |
| PCB body (non-optical areas) | COAT OK |

---

## 6. IMU — BNO055 (most units) / BNO085 (some units)

> **Variant note:** Most WaterCam units use the **BNO055**. A small number of units use the **BNO085** (or BNO086). Both share the same LGA-28 footprint and identical conformal coating rules. The differences that matter for this document are I2C address selection and package thickness (see table). Verify which variant is installed before finalizing firmware and I2C address conflict checks.

| Attribute | BNO055 | BNO085 |
|---|---|---|
| Package | LGA-28 | LGA-28 |
| Dimensions | 3.8 × 5.2 × 1.13 mm | 3.8 × 5.2 × 1.1 mm |
| Manufacturer | Bosch Sensortec | Bosch sensors + CEVA SH-2 firmware |
| Interface | I2C or SPI | I2C, SPI, or UART |
| I2C address select | COM3 pin → 0x28 (default) or 0x29 | SA0 pin selectable |
| Operating voltage (VDD) | 2.4–3.6 V | 2.4–3.6 V |

### Keep-Out Zones

**Magnetic keep-out.** The BNO085 contains a triaxial magnetometer used for heading/orientation. Keep the following away from the sensor:

- Power inductors and ferrite beads: minimum 15 mm distance recommended
- Permanent magnets and ferromagnetic hardware (screws, standoffs): minimum 10 mm
- DC motor drivers and high-current traces: route away from BNO085 footprint
- Large copper pours (ground planes) directly underneath can distort the magnetic field; avoid a copper-free zone beneath the sensor on adjacent layers if magnetometer accuracy is critical

**Axis alignment.** The BNO085 coordinate system is defined relative to chip orientation. For IMU-assisted georeferencing, document the rotation matrix between chip axes and geographic/enclosure axes at design time. This is especially important if the board is mounted at a non-standard angle inside the enclosure.

**Vibration isolation.** If the enclosure is subject to vibration from wind or water flow, consider vibration-damping foam under the PCB in the sensor region to avoid accelerometer saturation or high gyro noise.

**Conformal coating.** Both the BNO055 and BNO085 are closed LGA packages with no exposed sensing ports. Both can be coated. Avoid thick coatings (>75 µm) that mechanically stress the LGA solder joints.

### Conformal Coating — BNO055 / BNO085

| Zone | Treatment |
|---|---|
| LGA body (either variant) | COAT OK — closed package, no exposed sensing port |
| PCB around sensor | COAT OK |
| Solder joints | COAT OK — confirm <75 µm thickness to avoid mechanical stress |

**Official Resources**
- BNO055 Datasheet (Bosch Sensortec): https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bno055-ds000.pdf
- BNO08X Datasheet Rev 1.17 (CEVA/Hillcrest): `Docs/instructions/BNO080_085-Datasheet.pdf`
- Adafruit BNO085 breakout guide: `Docs/instructions/Adafruit-9-DOF-orientation-IMU-fusion-breakout-BNO085.pdf`
- Calibration procedure: `Docs/instructions/BNO080-BNO085-Sensor-Calibration-Procedure.pdf`

---

## 7. AHT20 (Aosong)

| Attribute | Value |
|---|---|
| Package | DFN-6 (3×3 mm) |
| Dimensions | 3.0 × 3.0 × 1.0 mm |
| Accuracy | ±2% RH, ±0.3°C (typical) |
| Interface | I2C, fixed address 0x38 |

### Keep-Out Zones

**Airflow / sensor opening.** The AHT20 has a rectangular sensing port on the top surface. This must be exposed to ambient air — do not cover it with conformal coating, epoxy, or any mechanical obstruction. If the sensor is inside an enclosure, the datasheet recommends a window in the housing that allows air exchange while minimizing the influence of air trapped inside the enclosure.

**Thermal isolation.** The AHT20 measures ambient temperature. Placing it near heat sources (voltage regulators, cellular modem, Pi SoC) will produce elevated temperature readings. The AHT20 datasheet (Aosong, Fig. 8–10) recommends adding PCB milling slots around the sensor footprint to reduce thermal conduction from the board.

**Humidity equilibration.** After exposure to solvents (flux cleaning, conformal coating), the AHT20 should be equilibrated at ambient conditions for 24 hours, or baked at >50°C for several hours, before taking calibrated readings.

### Conformal Coating — AHT20

```
AHT20 DFN-6 (3 mm × 3 mm) — Top View

  ┌─────────────────┐
  │  ┌───────────┐  │
  │  │  Sensing  │  │
  │  │   port    │  │ ← MASK this opening
  │  │  ██████   │  │    before coating
  │  └───────────┘  │
  │                 │
  │  DFN body       │ ← COAT OK (except port)
  └─────────────────┘
```

| Zone | Treatment |
|---|---|
| Sensor port (rectangular opening, top surface) | **MASK** — must be open to ambient air |
| DFN package body | COAT OK |
| PCB around AHT20 | COAT OK |

> **Post-coating:** Remove AHT20 sensor port mask last. Allow 24 h equilibration at ambient before trusting humidity/temperature readings.

**Official Resources**
- Datasheet (Aosong official): https://www.aosong.com/userfiles/files/media/Data%20Sheet%20AHT20.pdf
- SparkFun mirror: https://cdn.sparkfun.com/assets/d/2/b/e/d/AHT20.pdf
- Adafruit guide: `Docs/instructions/Adafruit-AHT20_TempHumiditySensor.pdf`

---

## 8. Quectel EC25 (LTE Cat 4)

> Confirm which form factor is in use: standard LCC module or Mini PCIe variant. Dimensions differ significantly.

| Attribute | LCC Module | Mini PCIe |
|---|---|---|
| Dimensions | 29.0 × 32.0 × 2.4 mm | 30.0 × 50.95 × 4.5 mm |
| Interface | LCC pads (solder to PCB) | Edge connector (52-pin) |
| Antenna | U.FL connector(s) | U.FL connector(s) |

### Keep-Out Zones (both variants)

**RF antenna keep-out.** The cellular antenna connects via U.FL. The U.FL connector requires a minimum 1 mm clearance on all sides for mating/unmating. The antenna cable/trace must route away from digital signal lines, switching regulators, and the LoRa module antenna.

**Under-module copper.** Quectel's hardware design guides specify that no copper pour or signal routing should be placed under the module footprint on the top layer.

**Thermal keep-out.** The EC25 generates meaningful heat during active data transmission. Keep temperature-sensitive components (AHT20, BNO085) at least 20 mm away or provide a thermal break.

**eSIM/SIM connector.** If using a nano-SIM socket, ensure clearance for card insertion/removal. If using the 1NCE eSIM, no physical access is needed.

### Conformal Coating — EC25

```
EC25 Module — Top View (LCC variant, 29 mm × 32 mm)

  ┌──────────────────────────────────┐
  │                                  │
  │   RF shield (metal can)          │
  │   COAT OK (exterior)             │
  │                                  │
  │                    ┌──┐          │
  │                    │  │ U.FL     │
  │                    │██│ MASK     │
  │                    └──┘          │
  │                                  │
  └──────────────────────────────────┘
  (LCC pads on underside — coat after soldering is OK)
```

| Zone | Treatment |
|---|---|
| EC25 module body (RF shield exterior) | COAT OK |
| U.FL connector(s) | **MASK** — coax cable must mate; coating prevents connection |
| SIM socket (if nano-SIM) | **MASK** — card insertion |
| LCC solder pads | COAT OK after soldering |

**Official Resources**
- Hardware Design v2.2: https://forums.quectel.com/uploads/short-url/yVwhmS9iLDp8K24V93xJw3L6zmS.pdf
- Hardware Design v1.3 (Pine64 mirror): https://files.pine64.org/doc/datasheet/project_anakin/LTE_module/Quectel_EC25_Hardware_Design_V1.3.pdf
- Quectel product page: https://www.quectel.com/product/lte-ec25-series/

---

## 9. MultiTech mDot (MTDOT-915 Series)

| Attribute | XBee Form (X1-SMA) | SMT Form (M1-UFL) |
|---|---|---|
| Dimensions | 33.02 × 27.69 × 3.81 mm | ~28.6 × 21.0 mm (verify) |
| Frequency | 915 MHz (US) | 915 MHz (US) |
| Antenna | SMA (external) or U.FL | U.FL |
| Interface | XBee socket (through-hole) or SMT | SMT pads |

### Keep-Out Zones

**RF keep-out.** For the SMA variant, a minimum 5 mm clear zone around the antenna connector body is required for connector access. For U.FL variants, apply the same 1 mm rule as the EC25 U.FL connectors. The LoRa antenna should be routed away from the cellular antenna (minimum 15–20 mm separation recommended for co-located antennas at 900 MHz vs. 700–2100 MHz cellular bands, or use orthogonal polarizations).

**Under-module copper.** Same rule as the EC25: no signal routing under the module footprint.

**XBee socket clearance.** If using the through-hole XBee socket variant, the socket adds approximately 4 mm in height below the PCB and requires clearance on both sides for the module to seat and unseat.

### Conformal Coating — mDot

| Zone | Treatment |
|---|---|
| mDot module body | COAT OK |
| U.FL connector(s) | **MASK** — coax cable must mate |
| SMA connector (if applicable) | **MASK** — antenna cable must thread on |
| XBee socket (if through-hole) | **MASK** — module must seat/unseat |

**Official Resources**
- Developer Guide with mechanical drawings (Chapter 2): https://multitech.com/wp-content/uploads/S000612-mDot-Developer-Guide.pdf
- Developer Guide DB9 rev: https://multitech.com/wp-content/uploads/s000612_DB9.pdf
- MultiTech developer resources: https://www.multitech.net/developer/products/multiconnect-dot-series/multiconnect-mdot/

---

## 10. HAT Stack Summary

When using the Pi 4B with the WittyPi 4 HAT stacked on top, the combined assembly height is approximately:

| Layer | Height |
|---|---|
| Pi 4B PCB + tallest component (USB-A) | ~16.4 mm (1.4 mm PCB + 15 mm component) |
| WittyPi 4 SMT header standoff | 3.5 mm |
| WittyPi 4 PCB thickness | ~1.6 mm |
| WittyPi 4 tallest component (approx.) | ~10 mm (DC/DC converter) |
| **Total stack height (approx.)** | **~33 mm** |

This is an estimate. Measure actual component heights against the WittyPi 4 STEP file and Pi 4B mechanical drawing to confirm enclosure sizing.

---

## 11. Special Placement Considerations

### I2C Bus Conflict Check

| Component | Default I2C Address | Notes |
|---|---|---|
| BNO055 (most units) | 0x28 (default) or 0x29 | COM3 pin selects; 0x28 is default |
| BNO085 (some units) | SA0 pin selectable | Confirm address against your specific board |
| AHT20 | 0x38 (fixed) | Cannot be changed |
| WittyPi 4 RTC (DS3231SN) | 0x68 (proxied by WittyPi MCU, configurable) | |
| WittyPi 4 temp sensor (MCP9808) | Proxied by WittyPi MCU | |

No address conflicts in the default BNO055 configuration (0x28/0x29 do not conflict with 0x38 or 0x68). Confirm WittyPi 4 I2C proxy address avoids 0x28, 0x29, and 0x38. For BNO085 units, confirm SA0 pin address selection does not conflict with WittyPi 4's proxied address.

### Antenna Separation

Keep the EC25 cellular antenna and mDot LoRa antenna physically separated and preferably orthogonally polarized. Minimum recommended center-to-center distance between antenna cables is 15 mm. Route antenna cables along opposite edges of the enclosure interior if possible.

---

## 12. Conformal Coating — Complete Mask Summary

| Component | Zone | Treatment | Reason |
|---|---|---|---|
| FLIR Lepton 3.5 | Entire module | **NO COAT, remove before coating** | Not a sealed assembly; contacts must wipe clean |
| FLIR Lepton 3.5 | Lens (internal) | **NO COAT** | Blocks LWIR 8–14 µm |
| FLIR Lepton 3.5 | Shutter pins | **NO COAT, NO OBSTRUCT** | Shutter actuation force |
| Lepton Breakout V2.0 | Molex 32-pin socket | **MASK** | Spring fingers must remain clean |
| Lepton Breakout V2.0 | Jumpers J5–J9 | **MASK** | Must remain reconfigurable |
| Lepton Breakout V2.0 | J2 20-pin header | **MASK** | Pi interface |
| Lepton Breakout V2.0 | Mounting holes | **MASK** | Thread engagement |
| Lepton Breakout V2.0 | PCB body | COAT OK | |
| Dorhea IR-Cut Camera | Lens assembly | **MASK** | Visible-light optical surface |
| Dorhea IR-Cut Camera | Image sensor | **MASK** | Permanent contamination |
| Dorhea IR-Cut Camera | IR-cut filter motor | **MASK** | Mechanical binding kills NIR capture |
| Dorhea IR-Cut Camera | CSI-2 FPC connector | **MASK** | Removable Pi connection |
| Dorhea IR-Cut Camera | PCB body | COAT OK | |
| AHT20 | Sensor port (top) | **MASK** | Must breathe ambient air |
| AHT20 | DFN body | COAT OK | |
| BNO055 / BNO085 | LGA body | COAT OK | Closed package; <75 µm thickness |
| Raspberry Pi 4B | USB-A, USB-C, RJ45, HDMI | **MASK** | Connector contacts |
| Raspberry Pi 4B | MicroSD slot | **MASK** | Card must insert |
| Raspberry Pi 4B | GPIO 40-pin header | **MASK** | WittyPi HAT connection |
| Raspberry Pi 4B | CSI-2 FPC connector | **MASK** | Camera FPC connection |
| Raspberry Pi 4B | DSI FPC connector | **MASK** | Future display or service |
| Raspberry Pi 4B | PCB body | COAT OK | |
| WittyPi 4 | 40-pin GPIO header | **MASK** | Contacts Pi GPIO |
| WittyPi 4 | DC screw terminal | **MASK** | Power wiring |
| WittyPi 4 | PCB body | COAT OK | |
| EC25 | U.FL connectors | **MASK** | Antenna coax |
| EC25 | SIM socket (if nano-SIM) | **MASK** | Card insertion |
| EC25 | Module body | COAT OK | |
| mDot | U.FL / SMA connectors | **MASK** | Antenna connection |
| mDot | XBee socket (if used) | **MASK** | Module seating |
| mDot | Module body | COAT OK | |

### Coating Process Order — Acrylic (Third-Party / Standard)

1. **Remove** FLIR Lepton module from Breakout Board socket. Set aside in anti-static bag.
2. Apply all masking materials (tape or peelable latex) to zones listed as MASK above.
3. Apply acrylic conformal coating (25–75 µm) by selective spray or brush. Do not flood coat — low pressure to avoid spray ingress under masking at connector edges.
4. Allow full cure (typically 30 min tack-free, 24 h full cure).
5. Remove all masking.
6. Inspect: no coating in socket, no coating on lens, no coating on filter motor.
7. Wait 24 h before trusting AHT20 humidity readings (solvent equilibration).
8. Reinstall FLIR Lepton module. Install board into enclosure.

> **Silicone builds:** See [Section 0 — Revised Process Order for Silicone](#revised-process-order--silicone-builds) for the extended procedure including 72 h off-gas period and AHT20 reference check.

---

## Document Notes

- Dimensions marked as "approx." or without a cited source should be measured from STEP/3D models or manufacturer drawings before use in a PCB layout.
- The WittyPi 4 SMT header height changed from 2.0 mm to 3.5 mm as of August 2025 (Adafruit product page). If your unit predates this change, adjust HAT stack height accordingly.
- The EC25 variant in use (LCC vs. Mini PCIe, and regional suffix: -AF, -A, etc.) determines the exact footprint. Verify against your specific part number.
- The mDot SMT variant dimensions should be confirmed against the MultiTech developer guide mechanical drawings (Chapter 2) for the specific MTDOT part number in use.
- **IMU variants (Rev 0.2):** Most WaterCam units use the BNO055 (I2C address 0x28/0x29 via COM3 pin). A small number of units use the BNO085 (CEVA SH-2 firmware, SA0 pin address select). Both are LGA-28; conformal coating rules are identical for both variants. Magnetic keep-out distances are the same.
- **Camera correction (Rev 0.2):** Prior revision listed "Raspberry Pi Camera Module 3." The installed optical camera is the Dorhea IR-Cut Camera (CSI-2, motorized NIR filter, GPIO 21).
- **Coating materials (Rev 0.3):** Acrylic is the specified coating for third-party-built units and is accurate for the use case (IP65 enclosure, secondary moisture protection). Student-assembled units may use MG modified silicone — see Section 0 for material comparison, outgassing risks, and the silicone-specific process order. Key difference: silicone builds require 72 h open-air off-gas before enclosure installation to protect the AHT20 humidity sensor.
