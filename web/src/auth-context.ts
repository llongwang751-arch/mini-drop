import { createContext, useContext } from "react";
import type { User } from "./types";
export const UserContext = createContext<User | null>(null);
export function useCanWrite() {
  const user = useContext(UserContext);
  return (
    !!user &&
    ["admin", "platform_admin", "group_admin", "operator"].includes(user.role)
  );
}
