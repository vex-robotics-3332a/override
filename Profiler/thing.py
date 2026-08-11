#pseudocode here! just want the formatting & cool colors
import numpy as np

# grabs points at twice the 'resolution' we want
for t in np.arange(0, 1, t_step/2):
    path.append(bezier.point_at(t))

#decimation: removing points that are too close to optimize the list
#Pseudocode warning! not actual python, just here to convey an idea
for point in path:
    if point.distance_to(previous_point) > RESOLUTION/2:
        path.remove(point)



    



