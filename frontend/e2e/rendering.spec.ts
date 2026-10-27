import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, rmdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

test("CCITT image masks render with bundled offline decoder", async ({ page }) => {
  const directory = mkdtempSync(join(tmpdir(), "pdfed-ccitt-"));
  const path = join(directory, "ccitt-mask.pdf");
  try {
  execFileSync(process.env.PYTHON ?? "python", ["-c", String.raw`
from PIL import Image, ImageDraw
import io, sys
im=Image.new('1',(256,256),1)
d=ImageDraw.Draw(im)
for y in range(16,240,24): d.rectangle((24,y,232,y+10),fill=0)
b=io.BytesIO(); im.save(b,format='TIFF',compression='group4')
b.seek(0); t=Image.open(b); off=t.tag_v2[273][0]; size=t.tag_v2[279][0]; data=b.getvalue()[off:off+size]
content=b'q 256 0 0 256 0 0 cm /Mask Do Q'
objects=[b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 256 256] /Resources << /XObject << /Mask 4 0 R >> >> /Contents 5 0 R >>', b'<< /Type /XObject /Subtype /Image /Width 256 /Height 256 /ImageMask true /BitsPerComponent 1 /Filter /CCITTFaxDecode /DecodeParms << /K -1 /Columns 256 /Rows 256 >> /Length '+str(len(data)).encode()+b' >>\nstream\n'+data+b'\nendstream', b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
out=b'%PDF-1.4\n'; offsets=[0]
for i,o in enumerate(objects,1): offsets.append(len(out)); out+=str(i).encode()+b' 0 obj\n'+o+b'\nendobj\n'
x=len(out); out+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(('%010d 00000 n \n'%v).encode() for v in offsets[1:])+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(x).encode()+b'\n%%EOF'
open(sys.argv[1],'wb').write(out)
`, path]);
  const decoders: string[] = [];
  page.on("response", response => {
    if (response.url().includes("/wasm/")) {
      expect(response.status()).toBe(200);
      decoders.push(response.url());
    }
  });
  await page.goto("/editor");
  await page.locator('input[type="file"][accept=".pdf,application/pdf"]').setInputFiles(path);
  const canvas = page.locator('[data-page-number="0"] canvas');
  await expect(canvas).toBeVisible();
  const dark = await canvas.evaluate((node: HTMLCanvasElement) => {
    const pixels = node.getContext("2d")!.getImageData(0, 0, node.width, node.height).data;
    let dark = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i]! < 80 && pixels[i + 1]! < 80 && pixels[i + 2]! < 80) dark++;
    }
    return dark;
  });
  expect(dark).toBeGreaterThan(10000);
  expect(decoders.some(url => url.endsWith("jbig2.wasm"))).toBe(true);
  } finally {
    rmSync(path, { force: true });
    rmdirSync(directory);
  }
});

