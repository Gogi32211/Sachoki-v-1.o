/** MD3 Divider */
export function Divider({ vertical = false, className = '' }) {
  if (vertical) {
    return <div className={`w-px self-stretch bg-md-outline-var ${className}`} />
  }
  return <hr className={`border-0 h-px bg-md-outline-var ${className}`} />
}
