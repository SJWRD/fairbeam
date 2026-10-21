/** File-picker filter for reference data (Touchstone s1p…s8p, CSV / text tables .txt/.dat/.tsv). */
export const REFERENCE_ACCEPT = ".s1p,.s2p,.s3p,.s4p,.s5p,.s6p,.s7p,.s8p,.snp,.ts,.txt,.dat,.csv,.tsv,text/plain,text/csv";

const referenceTypes = REFERENCE_ACCEPT.split(",");

/** Match the picker for drops too. Touchstone readers also accept port counts above eight;
 * HTML accept filters cannot express that extension pattern. */
export function acceptsReferenceFile(name: string, type = ""): boolean {
  const lowerName = name.toLowerCase();
  return referenceTypes.some(value => value.startsWith(".") ? lowerName.endsWith(value) : type.toLowerCase() === value)
    || /\.s[1-9]\d*p$/i.test(name);
}
