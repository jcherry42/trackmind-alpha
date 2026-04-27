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
          borderTopColor: colors.borderHairline,
          borderTopWidth: 1,
          height: Platform.OS === "ios" ? 86 : 66,
          paddingTop: 10,
          paddingBottom: Platform.OS === "ios" ? 26 : 10,
        },
        tabBarLabelStyle: {
          fontSize: 9,
          fontWeight: "700",
          letterSpacing: 1.2,
          textTransform: "uppercase",
          marginTop: 4,
        },
        tabBarIconStyle: { marginTop: 0 },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Dash",
          tabBarButtonTestID: "tab-dash",
          tabBarIcon: ({ color }) => <Feather name="grid" color={color} size={18} />,
        }}
      />
      <Tabs.Screen
        name="signals"
        options={{
          title: "Signals",
          tabBarButtonTestID: "tab-signals",
          tabBarIcon: ({ color }) => <Feather name="zap" color={color} size={18} />,
        }}
      />
      <Tabs.Screen
        name="positions"
        options={{
          title: "Positions",
          tabBarButtonTestID: "tab-positions",
          tabBarIcon: ({ color }) => <Feather name="briefcase" color={color} size={18} />,
        }}
      />
      <Tabs.Screen
        name="micro"
        options={{
          title: "Micro",
          tabBarButtonTestID: "tab-micro",
          tabBarIcon: ({ color }) => <Feather name="git-merge" color={color} size={18} />,
        }}
      />
      <Tabs.Screen
        name="analytics"
        options={{
          title: "Analytics",
          tabBarButtonTestID: "tab-analytics",
          tabBarIcon: ({ color }) => <Feather name="trending-up" color={color} size={18} />,
        }}
      />
    </Tabs>
  );
}
