export function validateVector(value) {
  if (!value || !Array.isArray(value.paths) || !value.paths.length || value.paths.length > 30000
      || typeof value.aspect !== 'number' || !Number.isFinite(value.aspect) || value.aspect < .00001 || value.aspect > 100000)
    throw new Error('El plano no contiene trazos o proporciones válidas.');
  let points = 0;
  const paths = value.paths.map(path => {
    if (!Array.isArray(path) || path.length < 4 || path.length % 2 || (points += path.length / 2) > 120000
        || !path.every(n => typeof n === 'number' && Number.isFinite(n) && n >= -.00000001 && n <= 1.00000001))
      throw new Error('El plano contiene coordenadas inválidas o supera 120,000 puntos.');
    return path.slice();
  });
  const source = {};
  for (const [key,item] of Object.entries(value.source || {})) {
    if (['name','format','projection','mode'].includes(key) && typeof item === 'string') source[key] = item.slice(0,240);
    if (['unit_scale','original_width_mm','original_height_mm'].includes(key) && typeof item === 'number' && Number.isFinite(item)) source[key] = item;
  }
  return {paths,aspect:value.aspect,source,warnings:Array.isArray(value.warnings) ? value.warnings.filter(v => typeof v === 'string').slice(0,20).map(v => v.slice(0,500)) : []};
}
