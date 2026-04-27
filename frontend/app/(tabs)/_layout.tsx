import React, { useEffect } from "react";
import { Tabs, useRouter } from "expo-router";
import { Feather } from "@expo/vector-icons";
import { Platform } from "react-native";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";

export default function TabsLayout() {
  const { user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (user === null) router.replace("/(auth)/login");
  }, [user, router]);

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.textPrimary,
        tabBarInactiveTintColor: colors.textTertiary,
        tabBarStyle: {
          backgroundColor: colors.bg,
          borderTopColor: colors.borderSubtle,
          borderTopWidth: 1,
          height: Platform.OS === "ios" ? 84 : 64,
          paddingTop: 6,
          paddingBottom: Platform.OS === "ios" ? 24 : 8,
        },
        tabBarLabelStyle: {
          fontSize: 10,
          fontWeight: "600",
          letterSpacing: 1.2,
          textTransform: "uppercase",
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Dash",
          tabBarIcon: ({ color, size }) => (
            <Feather name="grid" color={color} size={size - 2} />
          ),
        }}
      />
      <Tabs.Screen
        name="signals"
        options={{
          title: "Signals",
          tabBarIcon: ({ color, size }) => (
            <Feather name="zap" color={color} size={size - 2} />
          ),
        }}
      />
      <Tabs.Screen
        name="positions"
        options={{
          title: "Positions",
          tabBarIcon: ({ color, size }) => (
            <Feather name="briefcase" color={color} size={size - 2} />
          ),
        }}
      />
      <Tabs.Screen
        name="micro"
        options={{
          title: "Micro",
          tabBarIcon: ({ color, size }) => (
            <Feather name="git-merge" color={color} size={size - 2} />
          ),
        }}
      />
      <Tabs.Screen
        name="analytics"
        options={{
          title: "Analytics",
          tabBarIcon: ({ color, size }) => (
            <Feather name="bar-chart-2" color={color} size={size - 2} />
          ),
        }}
      />
    </Tabs>
  );
}
