export const isObjectEmpty = (
  obj: Record<string, unknown> | Map<string, unknown>,
): boolean => {
  if (!obj) return true;
  if (obj instanceof Map) return obj.size === 0;
  return Object.keys(obj).length === 0;
};

export const getEnumKeyByValue = <T extends object>(
  value: string,
  enumObj: T,
): T[keyof T] => {
  const key = Object.keys(enumObj).find(
    (key) => enumObj[key as keyof T] === value,
  );
  return enumObj[key as keyof T];
};

export const arraysEqual = <T extends {}>(a: T[], b: T[]): boolean => {
  if (!a || !b) return false;
  if (a.length !== b.length) return false;

  return a.every((item, index) => {
    const compareItem = b[index];
    if (!compareItem) return false;

    const keys = Object.keys(item) as Array<keyof T>;

    return keys.every((key) => item[key] === compareItem[key]);
  });
};
