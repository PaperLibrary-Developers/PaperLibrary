import assert from 'node:assert/strict'
import test from 'node:test'
import { getDocument, GlobalWorkerOptions, OPS } from '../../vendor/pdfjs/legacy/build/pdf.mjs'

function spotColorPdf () {
  const drawing = [
    '0 g /Spot cs 10 10 10 10 re f',
    '0 G /Spot CS 25 10 10 10 re S',
    '0 g /Spot cs 0 scn 40 10 10 10 re f',
    '0 g /Multi cs 55 10 10 10 re f'
  ].join('\n')
  const tintFunction = '{ add .25 mul .25 add .5 .75 }'
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 80 30] /Contents 4 0 R /Resources << /ColorSpace << /Spot [/Separation /Panel /DeviceRGB 5 0 R] /Multi [/DeviceN [/First /Second] /DeviceRGB 6 0 R] >> >> >>',
    `<< /Length ${drawing.length} >>\nstream\n${drawing}\nendstream`,
    '<< /FunctionType 2 /Domain [0 1] /C0 [1 1 1] /C1 [.2 .4 .6] /N 1 >>',
    `<< /FunctionType 4 /Domain [0 1 0 1] /Range [0 1 0 1 0 1] /Length ${tintFunction.length} >>\nstream\n${tintFunction}\nendstream`
  ]
  let pdf = '%PDF-1.4\n'
  const offsets = objects.map((object, i) => {
    const offset = pdf.length
    pdf += `${i + 1} 0 obj\n${object}\nendobj\n`
    return offset
  })
  const xref = pdf.length
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  for (const offset of offsets) pdf += `${String(offset).padStart(10, '0')} 00000 n \n`
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return new TextEncoder().encode(pdf)
}

test('PDF reader applies initial Separation and DeviceN tints without an explicit color operator', async () => {
  GlobalWorkerOptions.workerSrc = new URL('../../vendor/pdfjs/build/pdf.worker.min.mjs', import.meta.url).href
  const loading = getDocument({ data: spotColorPdf() })
  try {
    const page = await (await loading.promise).getPage(1)
    const { fnArray, argsArray } = await page.getOperatorList()
    const colors = fnArray.flatMap((op, i) => {
      if (op === OPS.setFillRGBColor) return [['fill', ...argsArray[i]]]
      if (op === OPS.setStrokeRGBColor) return [['stroke', ...argsArray[i]]]
      return []
    })
    assert.deepEqual(colors, [
      ['fill', 0, 0, 0], ['fill', 51, 102, 153],
      ['stroke', 0, 0, 0], ['stroke', 51, 102, 153],
      ['fill', 0, 0, 0], ['fill', 51, 102, 153], ['fill', 255, 255, 255],
      ['fill', 0, 0, 0], ['fill', 191, 128, 191]
    ])
  } finally {
    await loading.destroy()
  }
})
