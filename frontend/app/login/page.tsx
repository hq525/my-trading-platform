"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: (pw: string) => api.login(pw),
    onSuccess: () => {
      window.location.href = "/";
    },
  });

  return (
    <div className="mx-auto mt-24 max-w-sm rounded-lg border border-gray-800 bg-gray-900 p-6">
      <h1 className="mb-4 text-lg font-semibold text-gray-100">Log in</h1>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          login.mutate(password);
        }}
        className="space-y-3"
      >
        <label className="block text-sm text-gray-400" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-gray-100 outline-none focus:border-gray-500"
          autoFocus
        />
        {login.error && (
          <p className="text-sm text-red-400">
            {login.error instanceof ApiError ? login.error.message : "Login failed"}
          </p>
        )}
        <button
          type="submit"
          disabled={login.isPending || password.length === 0}
          className="w-full rounded bg-emerald-700 px-3 py-2 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          Log in
        </button>
      </form>
    </div>
  );
}
