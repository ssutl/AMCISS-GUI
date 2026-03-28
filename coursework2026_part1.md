# EEEN40412 Coursework 2026 — Part 1: Tunnel Device

**Student:** Shay Campbell — 11079275  
**Due:** 17 May 2026

---

## Given Parameters

| Symbol | Value |
|---|---|
| E | 1.2 eV (incident electron energy) |
| V₀ | 2.0 eV (barrier height) |
| 2a | 2 nm → a = 1 nm (barrier width) |
| d | 20 nm (barrier separation) |
| mₑ | 9.109 × 10⁻³¹ kg |
| ℏ | 1.055 × 10⁻³⁴ J·s |

**Derived quantities:**

$$k = \frac{\sqrt{2m_e E}}{\hbar} = \frac{\sqrt{2 \times 9.109\times10^{-31} \times 1.2 \times 1.6\times10^{-19}}}{1.055\times10^{-34}} \approx 5.61 \text{ nm}^{-1}$$

$$\kappa = \frac{\sqrt{2m_e(V_0 - E)}}{\hbar} = \frac{\sqrt{2 \times 9.109\times10^{-31} \times 0.8 \times 1.6\times10^{-19}}}{1.055\times10^{-34}} \approx 4.58 \text{ nm}^{-1}$$

Since 2κa = 2 × 4.58 × 1 = 9.16 ≫ 1 (deep tunnelling regime):
- |t| ≈ 2 × 10⁻⁴ (small transmission amplitude)
- |r| ≈ 1 (nearly total internal reflection)

Free region between barriers: a < x < d − a, width = d − 2a = **18 nm**

---

## 1a) Reflection and Transmission Amplitudes (6 marks)

**Goal:** Show that r and t are independent of barrier position — i.e. M(a) is the same for cases a) and b).

**General wavefunction for barrier with left face at arbitrary position x₀ (spanning x₀ to x₀ + 2a):**

$$\psi(x) = \begin{cases} e^{ikx} + r\,e^{-ikx} & x < x_0 \\ C\,e^{\kappa(x-x_0)} + D\,e^{-\kappa(x-x_0)} & x_0 < x < x_0+2a \\ t\,e^{ikx} & x > x_0+2a \end{cases}$$

**Boundary conditions at x = x₀ (continuity of ψ and ψ'):**

$$e^{ikx_0} + r\,e^{-ikx_0} = C + D \tag{1}$$

$$ik\left(e^{ikx_0} - r\,e^{-ikx_0}\right) = \kappa(C - D) \tag{2}$$

**Boundary conditions at x = x₀ + 2a:**

$$C\,e^{2\kappa a} + D\,e^{-2\kappa a} = t\,e^{ik(x_0+2a)} \tag{3}$$

$$\kappa\left(C\,e^{2\kappa a} - D\,e^{-2\kappa a}\right) = ik\,t\,e^{ik(x_0+2a)} \tag{4}$$

**Solve (3) and (4) for C and D:**

$$C = \frac{t}{2}e^{ik(x_0+2a)-2\kappa a}\left(1+\frac{ik}{\kappa}\right), \qquad D = \frac{t}{2}e^{ik(x_0+2a)+2\kappa a}\left(1-\frac{ik}{\kappa}\right)$$

**Substitute into (1) and (2) and solve for t:**

$$\boxed{t = \frac{e^{-2ika}}{\cosh(2\kappa a) + \dfrac{i}{2}\!\left(\dfrac{\kappa}{k}-\dfrac{k}{\kappa}\right)\sinh(2\kappa a)}}$$

The expression for t contains **no x₀** — the transmission amplitude is **independent of barrier position**. ✓

**Solving for r:**

$$r = -\frac{e^{2ikx_0}\cdot\dfrac{i}{2}\!\left(\dfrac{k}{\kappa}+\dfrac{\kappa}{k}\right)\sinh(2\kappa a)}{\cosh(2\kappa a)+\dfrac{i}{2}\!\left(\dfrac{\kappa}{k}-\dfrac{k}{\kappa}\right)\sinh(2\kappa a)}$$

The phase factor e^{2ikx₀} shifts the complex phase of r but **|r|² is position-independent**. The transfer matrix M(a) is defined using amplitudes evaluated locally at the barrier edges, removing this phase factor — making M(a) purely a function of k, κ, and a, independent of position x₀. ✓

---

## 1b) Electron Wavefunction in the Inter-Barrier Region

**Geometry:**
- Left barrier: 0 < x < 2a (inner face at x = a)
- Right barrier: d < x < d + 2a (inner face at x = d − a)  
- Free region: a < x < d − a

---

### 1b(i) — Forward only, no reflection from right barrier (8 marks)

The electron tunnels through the left barrier. No boundary condition applied at the right barrier.

**Wavefunction:**

$$\psi(x) = t\,e^{ikx}, \qquad a < x < d-a$$

**Derivation:** After tunnelling through the left barrier, the transmitted amplitude is t. Without interaction with the right barrier, only the rightward-propagating plane wave exists.

**Probability density:**

$$|\psi(x)|^2 = |t|^2 \approx 4 \times 10^{-8} = \text{constant}$$

**Plot:** Flat horizontal line at height |t|² throughout the inter-barrier region.

---

### 1b(ii) — Transmit through left, single reflection at x = d − a (8 marks)

The forward wave t·e^{ikx} hits the inner face of the right barrier at x = d − a and reflects with amplitude r.

**Derivation:** The incident wave at x = d − a has amplitude t·e^{ik(d−a)}. After reflection, the backward wave at x = d − a is r·t·e^{ik(d−a)}. Propagating leftward from d − a:

$$\psi_\text{back}(x) = r\cdot t\cdot e^{ik(d-a)} \cdot e^{-ik(x-(d-a))} = r\cdot t\cdot e^{2ik(d-a)}\,e^{-ikx}$$

**Total wavefunction:**

$$\boxed{\psi(x) = t\,e^{ikx} + r\,t\,e^{2ik(d-a)}\,e^{-ikx}}$$

**Probability density:**

$$|\psi(x)|^2 = |t|^2\left|1 + r\,e^{2ik(d-a-x)}\right|^2 = |t|^2\left[1 + |r|^2 + 2|r|\cos\!\left(2k(d-a-x) + \arg(r)\right)\right]$$

**Plot:** Oscillating (standing wave fringes) with spatial period:

$$\Lambda = \frac{\pi}{k} = \frac{\pi}{5.61} \approx 0.56 \text{ nm}$$

---

### 1b(iii) — Transmit through left, reflect at d−a, reflect again at x = a (8 marks)

The backward wave from (ii) reaches the inner face of the left barrier at x = a and reflects again with amplitude r.

**Derivation of third term:** The backward wave at x = a has amplitude:

$$r\cdot t\cdot e^{2ik(d-a)}\cdot e^{-ika}$$

Reflecting with coefficient r and propagating rightward:

$$\psi_\text{3rd}(x) = r^2\cdot t\cdot e^{2ik(d-a)}\cdot e^{-ika}\cdot e^{ik(x-a)} = r^2\cdot t\cdot e^{2ik(d-2a)}\,e^{ikx}$$

**Total wavefunction:**

$$\boxed{\psi(x) = t\,e^{ikx} + r\,t\,e^{2ik(d-a)}\,e^{-ikx} + r^2\,t\,e^{2ik(d-2a)}\,e^{ikx}}$$

$$= t\,e^{ikx}\!\left(1 + r^2 e^{2ik(d-2a)}\right) + r\,t\,e^{2ik(d-a)}\,e^{-ikx}$$

**Plot:** Asymmetric interference pattern — amplitude enhanced near x = a compared to case (ii), as the third wave adds constructively to the first.

---

### 1b(iv) — Infinite reflections, geometric series (10 marks)

Grouping forward-propagating and backward-propagating contributions separately:

**Forward waves** (0, 2, 4, ... reflections — each round trip adds a factor r²e^{2ik(d−2a)}):

$$F(x) = t\,e^{ikx}\sum_{n=0}^{\infty}\left[r^2 e^{2ik(d-2a)}\right]^n = \frac{t\,e^{ikx}}{1 - r^2\,e^{2ik(d-2a)}}$$

**Backward waves** (1, 3, 5, ... reflections):

$$B(x) = r\,t\,e^{2ik(d-a)}\,e^{-ikx}\sum_{n=0}^{\infty}\left[r^2 e^{2ik(d-2a)}\right]^n = \frac{r\,t\,e^{2ik(d-a)}\,e^{-ikx}}{1 - r^2\,e^{2ik(d-2a)}}$$

where z = r²e^{2ik(d−2a)} and |z| = |r|² < 1 so the series converges.

**Total wavefunction:**

$$\boxed{\psi(x) = \frac{t\left[e^{ikx} + r\,e^{2ik(d-a)}\,e^{-ikx}\right]}{1 - r^2\,e^{2ik(d-2a)}}}$$

**Probability density:**

$$|\psi(x)|^2 = \frac{|t|^2\left|1 + r\,e^{2ik(d-a-x)}\right|^2}{\left|1 - r^2\,e^{2ik(d-2a)}\right|^2}$$

**Physical interpretation:** The denominator |1 − r²e^{2ik(d−2a)}|² can approach zero at resonance when:

$$k(d-2a) = n\pi, \quad n \in \mathbb{Z}$$

At resonance, the wavefunction amplitude is dramatically enhanced (Fabry-Perot resonance) and transmission through the double barrier approaches unity even though each individual barrier has very low transmission.

**Numerical check (d = 20 nm):**

$$k(d-2a) = 5.61 \times 18 \approx 101 \text{ rad} \approx 32.1\pi$$

Not exactly at resonance (would need integer multiple of π), so amplitude is large but finite.

**Plot:** Same oscillatory pattern as (ii) and (iii) but with amplitude multiplied by 1/|1 − r²e^{2ik(d−2a)}|² — significantly enhanced standing wave inside the cavity compared to cases (i)–(iii).
