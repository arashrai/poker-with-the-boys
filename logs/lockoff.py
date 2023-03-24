from collections import defaultdict

for x in range(0, 100):
    for y in range(0, 100):
        for z in range(0, 100):
    
            teams = defaultdict(int)
            teams[2] = x
            teams[3] = y
            teams[4] = z

            while teams[2] > 1 or teams[3] > 1 or teams[4] > 1:
                for k in (2, 3, 4):
                    v = teams[k]
                    if v >= 2:
                        num_matches = v // 2

                        teams[k+1] += num_matches
                        teams[k-1] += num_matches
                        teams[k] -= num_matches * 2

            if teams[2] == teams[3] == teams[4] == 0:
                print("Starting teams of 2:", x)
                print("Starting teams of 3:", y)
                print("Starting teams of 4:", z)
                print("Teams of 1:", teams[1])
                print("Teams of 2:", teams[2])
                print("Teams of 3:", teams[3])
                print("Teams of 4:", teams[4])
                print("Teams of 5:", teams[5])
                print()
