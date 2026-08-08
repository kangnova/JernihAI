# Set Uji Gambar (Fase 0 — prd.md §13)

Folder ini berisi gambar uji untuk development dan evaluasi kualitas.

## Generate gambar sintetis

```bash
python scripts/make_test_images.py
```

Menghasilkan `gradient_512.png`, `noisy_256.png`, `pattern_1024.png`
(stdlib-only, tanpa dependensi ML — aman di laptop dev tanpa AVX2).

## Foto real (untuk evaluasi Fase 1)

Untuk set evaluasi PSNR/SSIM + NIQE/BRISQUE (prd.md §10), gunakan foto
berlisensi bebas (Unsplash / Pexels), simpan di subfolder `real/`, dan
catat sumber lisensinya.
